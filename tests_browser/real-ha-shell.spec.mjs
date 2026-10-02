import {expect, test} from "@playwright/test";
import {managementRouteState, waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";
import {retentionGrowth, sampleRetainedRuntime} from "./browser-retention-metrics.mjs";
import {mkdir, readFile, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

async function nativeEvidence(label, data) {
  const path = process.env.EOAI_NATIVE_EVIDENCE;
  expect(path).toBeTruthy();
  let reports = {};
  try { reports = JSON.parse(await readFile(path, "utf8")); }
  catch (error) { if (error.code !== "ENOENT") throw error; }
  reports[label] = data;
  await mkdir(dirname(path), {recursive:true});
  await writeFile(path, JSON.stringify(reports));
}

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;

test.describe("nightly ownership", () => {
  test.skip(process.env.EOAI_NATIVE_OWNERSHIP !== "1", "Enhanced native ownership profile only");
  for (const phase of ["validation", "commit"]) for (const completion of ["success", "failure"]) {
    test(`Function Tool ${phase} ${completion} cannot take over another editor`, async ({context, page, request}) => {
      const panel = await openColdHaRoute(context, page, "capabilities/functions");
      const prefix = `owned_${phase}_${completion}`;
      const alpha = `${prefix}_alpha`, beta = `${prefix}_beta`, renamed = `${alpha}_renamed`;
      const tool = (name, marker) => ({spec:{name, description:marker, parameters:{type:"object", properties:{}}}, function:{type:"template", value_template:marker}});
      await panel.evaluate(async (host, {alpha, beta, tools}) => {
        for (const value of tools) await host._call("tools", "save", {tool:value});
        await host._call("tools", "save_group", {group:{id:alpha, name:alpha, description:"Alpha association", loading_mode:"on_demand", enabled:true, functions:[alpha]}});
        const policy = await host._call("guest_mode", "get");
        await host._call("guest_mode", "save_policy", {revision:policy.revision, config:{...policy.config, guest_allowed_function_names:[alpha, beta]}});
        const rules = await host._call("request_rules", "list");
        await host._call("request_rules", "create", {revision:rules.revision, rule:{name:alpha, phrases:[alpha], match_type:"equals", enabled:true, action_type:"local_action", action:{actions:[{type:"function", function:alpha, arguments:{}}], success_response:"Alpha action"}}});
        await host._loadSection(true);
      }, {alpha, beta, tools:[tool(alpha, "ALPHA_ORIGINAL"), tool(beta, "BETA_ORIGINAL")]});
      await panel.locator(`[data-tool-key="${alpha}"] .edit-tool`).click();
      const yaml = JSON.stringify(tool(renamed, "ALPHA_SUBMITTED_IMPLEMENTATION"));
      await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), yaml);
      await panel.evaluate((host, {phase, yaml, alpha}) => {
        const original = host.hass.callWS.bind(host.hass);
        window.__ownedRequests = [];
        window.__ownedHeld = null;
        host.hass.callWS = async message => {
          if (message.section === "tools") window.__ownedRequests.push(structuredClone(message));
          return original(message);
        };
        const call = host._call.bind(host);
        host._call = async (section, action, params = {}) => {
          const result = await call(section, action, params);
          const message = {section, action, ...params};
          if (!window.__ownedHeld && section === "tools"
              && (phase === "validation" ? action === "validate_yaml" && params.yaml === yaml
                : action === "save" && params.original_name === alpha)) {
            window.__ownedHeld = {message:structuredClone(message), result:structuredClone(result)};
            await new Promise((resolve, reject) => {window.__ownedRelease = {resolve, reject};});
            window.__ownedSettled = true;
          }
          return result;
        };
      }, {phase, yaml, alpha});
      await panel.locator("#tool-save").click();
      await expect.poll(() => page.evaluate(() => Boolean(window.__ownedHeld))).toBe(true);
      const held = await page.evaluate(() => window.__ownedHeld);
      if (phase === "validation") { expect(held.message.yaml).toBe(yaml); expect(held.result.valid).toBe(true); }
      else { expect(held.message.tool.function.value_template).toBe("ALPHA_SUBMITTED_IMPLEMENTATION"); expect(held.result.functions.some(t => t.spec.name === renamed)).toBe(true); }
      await panel.locator("#tool-cancel").click();
      await panel.locator(`[data-tool-key="${beta}"] .edit-tool`).click();
      const betaYaml = JSON.stringify(tool(beta, "BETA_UNSAVED_DRAFT"));
      await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), betaYaml);
      const status = await panel.locator("#tool-error").textContent();
      await panel.evaluate((host, completion) => {
        window.__oldEditorSave = host._pendingMutations ? [...host._pendingMutations.values()] : [];
        if (completion === "failure") window.__ownedRelease.reject(new Error("Old acknowledgement failed"));
        else window.__ownedRelease.resolve();
      }, completion);
      // A round trip on the same socket follows the released completion; then
      // drain microtasks and the panel's serialized mutation tail.
      await panel.evaluate(async host => {
        await host.hass.callWS({type:"config/entity_registry/list"});
        await host._eocFunctionMutationTail?.catch(() => {});
      });
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
      await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(e => e.yaml)).toBe(betaYaml);
      await expect(panel.locator("#tool-error")).toHaveText(status);
      await expect(panel.locator("#tool-save")).toBeEnabled();
      await expect(panel.locator("#tool-save")).toHaveText("Save");
      const saves = await page.evaluate(() => window.__ownedRequests.filter(m => m.action === "save"));
      expect(saves).toHaveLength(phase === "commit" ? 1 : 0);
      expect(saves.some(m => m.original_name === beta)).toBe(false);
      const snapshot = await panel.evaluate(async host => ({config:await host._call("configuration", "get"), rules:await host._call("request_rules", "list")}));
      const expectedAlpha = phase === "commit" ? renamed : alpha;
      expect(snapshot.config.config.functions.find(t => t.spec.name === beta).function.value_template).toBe("BETA_ORIGINAL");
      expect(snapshot.config.config.function_groups.find(g => g.id === alpha).functions).toEqual([expectedAlpha]);
      expect(snapshot.config.config.guest_allowed_function_names).toEqual([expectedAlpha, beta]);
      expect(JSON.stringify(snapshot.rules.rules.find(r => r.name === alpha).action)).toContain(expectedAlpha);
      // The committed Alpha revision is current; Beta saves through the normal UI.
      await panel.locator("#tool-save").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      const reloaded = await (await request.post(`${process.env.REAL_HA_OWNERSHIP_CONTROL}/reload`)).json();
      expect(reloaded.functions.find(t => t.spec.name === beta).function.value_template).toBe("BETA_UNSAVED_DRAFT");
      expect(reloaded.function_groups.find(g => g.id === alpha).functions).toEqual([expectedAlpha]);
      await page.reload();
      await expect(panel.locator(`#agent`)).toBeEnabled();
      await panel.locator(`[data-tool-key="${beta}"] .edit-tool`).click();
      await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(e => e.yaml)).toContain("BETA_UNSAVED_DRAFT");
      await panel.locator("#tool-cancel").click();
    });
  }

  test("satellite registry replacement resolves the current native selection in one document", async ({context, page, request}) => {
    const panel = await openColdHaRoute(context, page, "assistant/voice");
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      window.__registryLists = 0; window.__voicePayloads = [];
      host.hass.callWS = async message => {
        if (message.type === "config/entity_registry/list") {
          window.__registryLists++;
        }
        if (message.section === "configuration" && ["save", "update"].includes(message.action)) window.__voicePayloads.push(structuredClone(message));
        return original(message);
      };
    });
    await panel.locator("#add-voice-mapping").click();
    const warm = await panel.evaluate(host => host.hass.callWS({type:"config/entity_registry/list"}));
    expect(warm.find(e => e.entity_id === "assist_satellite.ownership_kitchen").device_id).toBe(process.env.REAL_HA_OLD_DEVICE);
    const registry = await (await request.post(`${process.env.REAL_HA_OWNERSHIP_CONTROL}/replace-registry`)).json();
    expect(registry.kitchen).toBe(process.env.REAL_HA_NEW_DEVICE);
    expect(registry.office).toBe(process.env.REAL_HA_OLD_DEVICE);
    const row = panel.locator("[data-voice-mapping-row]").last();
    const picker = row.locator("ha-entity-picker");
    await picker.locator("ha-picker-field").click();
    await picker.locator("ha-combo-box-item").filter({hasText:"assist_satellite.ownership_kitchen"}).locator("button").click();
    await expect(row.locator(".voice-device-id")).toHaveValue(process.env.REAL_HA_NEW_DEVICE);
    await row.locator(".voice-owner-type").selectOption("user");
    const user = row.locator("ha-user-picker");
    await user.locator("ha-picker-field").click();
    await user.locator("ha-combo-box-item").filter({hasText:process.env.REAL_HA_PICKER_USER_NAME}).locator("button").click();
    await panel.locator("#save-config").click();
    await expect(panel.locator("#save-config")).toBeEnabled();
    const payloads = await page.evaluate(() => window.__voicePayloads);
    expect(payloads.length).toBeGreaterThan(0);
    expect(payloads.at(-1).config.voice_device_mappings[process.env.REAL_HA_NEW_DEVICE]).toBe(`user:${process.env.REAL_HA_SMOKE_USER_ID}`);
    const read = await panel.evaluate(host => host._call("configuration", "get"));
    expect(read.config.voice_device_mappings[process.env.REAL_HA_OLD_DEVICE]).toBe("user:ownership-office-user");
    expect(read.config.voice_device_mappings[process.env.REAL_HA_NEW_DEVICE]).toBe(`user:${process.env.REAL_HA_SMOKE_USER_ID}`);
    const reloaded = await (await request.post(`${process.env.REAL_HA_OWNERSHIP_CONTROL}/reload`)).json();
    expect(reloaded.voice_device_mappings).toEqual(read.config.voice_device_mappings);
    const probes = await (await request.post(`${process.env.REAL_HA_OWNERSHIP_CONTROL}/probe-voice`)).json();
    expect(probes).toEqual({owners:2, private_markers:2, authenticated_users:[null, null]});
    await page.reload();
    await expect(panel.locator("#agent")).toBeEnabled();
    expect((await panel.evaluate(host => host._call("configuration", "get"))).config.voice_device_mappings).toEqual(read.config.voice_device_mappings);
  });

  test("failed initial satellite registry request retries in the same panel", async ({context, page}) => {
    const panel = await openColdHaRoute(context, page, "assistant/basics");
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      window.__registryLists = 0; window.__registryFailures = 0;
      host.hass.callWS = async message => {
        if (message.type === "config/entity_registry/list") {
          window.__registryLists++;
          if (window.__registryLists === 1) {window.__registryFailures++; throw new Error("Controlled registry outage");}
        }
        return original(message);
      };
      host._navigate("assistant", "voice");
    });
    await expect.poll(() => page.evaluate(() => window.__registryFailures)).toBe(1);
    const row = panel.locator("[data-voice-mapping-row]").last();
    const picker = row.locator("ha-entity-picker");
    await picker.locator("ha-picker-field").click();
    await picker.locator("ha-combo-box-item").filter({hasText:"assist_satellite.ownership_kitchen"}).locator("button").click();
    await expect(row.locator(".voice-device-id")).toHaveValue(process.env.REAL_HA_NEW_DEVICE);
    expect(await page.evaluate(() => window.__registryLists)).toBeGreaterThan(1);
  });
});

const lifetimeYaml = description => `spec:\n  name: native_lifetime_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: lifetime-healthy\n`;

async function nativeRoute(panel, path) {
  const [section, subsection] = path.split("/");
  await panel.evaluate((host, [section, subsection]) => host._navigate(section, subsection), [section, subsection]);
  await waitForManagementRouteReady(panel.page(), {name:path, path}, 30000);
}

test.describe("nightly native", () => {
  test.skip(process.env.EOAI_NATIVE_ENDURANCE !== "1", "Enhanced native lifetime profile only");

  test("native Composite rejection preserves the saved tool and editable recovery", async ({context, page}) => {
    const panel = await openColdHaRoute(context, page, "capabilities/functions");
    await panel.locator("#function-add").click();
    await panel.locator("#add-tool").click();
    const valid = lifetimeYaml("Authoritative Composite boundary").replace("native_lifetime_tool", "native_composite_probe");
    await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), valid);
    await panel.locator("#tool-save").click();
    const card = panel.locator('[data-tool-key="native_composite_probe"]');
    await expect(card).toContainText("Authoritative Composite boundary");
    await card.locator(".edit-tool").click();
    let functionBody = {type:"template", value_template:"Rejected execution"};
    for (let depth = 0; depth < 33; depth++) functionBody = {type:"composite", sequence:[functionBody]};
    const tool = {spec:{name:"native_composite_probe", description:"Rejected depth draft", parameters:{type:"object", properties:{}}}, function:functionBody};
    await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), JSON.stringify(tool));
    await panel.locator("#tool-validate").click();
    await expect(panel.locator("#tool-error")).toContainText(/depth|nested/i);
    await panel.locator("#tool-save").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-save")).toBeEnabled();
    await panel.locator("#tool-cancel").click();
    await card.locator(".edit-tool").click();
    await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(element => element.yaml)).toContain("Authoritative Composite boundary");
    await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), valid.replace("Authoritative Composite boundary", "Recovered native validation"));
    await page.keyboard.press("Control+s");
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await expect(card).toContainText("Recovered native validation");
    await nativeEvidence("composite", {native_composite_rejections:1, native_composite_recoveries:1});
  });

  test("abandoned real upload consumes quota until elapsed expiry then reconnect recovers", async ({context, page, request}, testInfo) => {
    test.setTimeout(120000);
    const panel = await openColdHaRoute(context, page, "usage-maintenance/backup-restore");
    await panel.locator("#transfer-export-mode").selectOption("full");
    const downloaded = page.waitForEvent("download");
    await panel.locator("#create-backup-transfer").click();
    const download = await downloaded;
    expect(await download.failure()).toBeNull();
    const archive = await download.path();
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async message => {
        const result = await original(message);
        if (message.action === "import_chunk" && !window.__uploadHeld) {
          window.__uploadHeld = {session:message.data.session_id, received:result.received};
          await new Promise(() => {});
        }
        return result;
      };
    });
    await panel.locator("#backup-file-transfer").setInputFiles(archive);
    await expect.poll(() => page.evaluate(() => Boolean(window.__uploadHeld))).toBe(true);
    const admitted = await page.evaluate(() => window.__uploadHeld);
    expect(admitted.received).toBeGreaterThan(0);
    await page.close();
    const state = async () => (await request.get(process.env.REAL_HA_TRANSFER_STATE)).json();
    expect(await state()).toMatchObject({imports:1, files_present:true});
    const replacement = await context.newPage();
    try {
      const fresh = await openColdHaRoute(context, replacement, "usage-maintenance/backup-restore");
      const blocked = await fresh.evaluate(async host => {
        const agent = host._selectedAgent();
        try {
          await host.hass.callWS({type:"extended_openai_conversation_responses/management/backup_transfer", action:"import_start", ...agent, data:{filename:"quota-probe.zip",size:4096}});
          return "unexpected admission";
        } catch (error) { return error.message; }
      });
      expect(blocked).toMatch(/active|transfer|limit|session/i);
      expect(blocked).not.toBe("unexpected admission");
      // No test clock jump or explicit cancellation: backend expiry is lazy.
      await replacement.waitForTimeout(11000);
      expect(await state()).toMatchObject({imports:1, files_present:true});
      await fresh.locator("#backup-file-transfer").setInputFiles(archive);
      await expect(fresh.locator("#restore-dialog")).toHaveJSProperty("open", true);
      await expect(fresh.locator("#restore-transfer-apply")).toBeEnabled();
      await fresh.locator("#restore-transfer-cancel").click();
      await expect.poll(async () => (await state()).imports).toBe(0);
      expect((await state()).owned_files_on_disk).toBe(0);
      await nativeEvidence("transfer", {native_abandoned_uploads:1, native_upload_bytes:admitted.received, native_transfer_reclaims:1});
      await testInfo.attach("native-transfer-abandonment", {body:JSON.stringify({admitted_bytes:admitted.received, quota_rejections:1, elapsed_expiry_seconds:10, successful_reconnects:1}), contentType:"application/json"});
    } finally { await replacement.close(); }
  });

  test("warmed widgets plateau in one document and Ctrl+S commits once", async ({context, page}, testInfo) => {
    test.setTimeout(300000);
    test.skip(testInfo.project.name !== "chromium", "CDP retained heap requires Chromium");
    const panel = await openColdHaRoute(context, page, "capabilities/functions");
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await nativeRoute(panel, "capabilities/request-rules");
    const ruleName = "Native retained condition";
    await panel.evaluate(async (host, name) => {
      await host._call("request_rules", "create", {revision:host._result.revision, rule:{name, phrases:["retained native condition"], match_type:"equals", action_type:"model_routing", action:{model:"gpt-5-mini", scope:"request", continue_to_ai:true, success_response:"Updated"}, conditions:[{condition:"template", value_template:"{{ false }}"}]}});
      await host._loadSection(true);
    }, ruleName);
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      window.__nativeMutations = [];
      host.hass.callWS = async message => {
        const result = await original(message);
        if (message.section === "tools" && message.action === "save") window.__nativeMutations.push(message);
        return result;
      };
    });
    const cycle = async index => {
      await nativeRoute(panel, "capabilities/functions");
      await panel.locator("#function-add").click();
      await panel.locator("#add-tool").click();
      await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), lifetimeYaml(`Cancelled ${index}`));
      await panel.locator("#tool-cancel").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      await panel.locator("#function-add").click();
      await panel.locator("#add-ha-tools").click();
      const discovery = panel.locator("dialog[data-ha-llm-tools-dialog]");
      await expect(discovery).toBeVisible();
      await expect(discovery.locator("[data-status]")).not.toContainText("Loading");
      await discovery.locator("[data-search]").fill(`cancel-${index}`);
      await discovery.locator("[data-cancel]").click();
      await expect(discovery).toHaveCount(0);
      await nativeRoute(panel, "capabilities/request-rules");
      await panel.locator(".request-rule-card").filter({hasText:ruleName}).locator(".rule-edit").click();
      const selector = panel.locator("#rule-condition-host > ha-selector");
      await expect(selector).toBeVisible();
      await expect.poll(() => selector.evaluate(element => Boolean(element.hass?.localize))).toBe(true);
      const row = selector.locator("ha-automation-condition-row").first();
      await expect(row).toBeVisible();
      await row.evaluate(element => {element._yamlMode = true; element.requestUpdate();});
      await row.locator("ha-expansion-panel").evaluate(element => {element.expanded = true;});
      const yaml = selector.locator("ha-yaml-editor");
      await expect.poll(() => yaml.evaluate(element => element.yaml)).toContain("false");
      await yaml.locator(".cm-content").fill("condition: template\nvalue_template: '{{ true }}'");
      await expect.poll(() => selector.evaluate(element => element.value[0]?.value_template)).toBe("{{ true }}");
      await panel.locator("#rule-dialog .rule-close").filter({hasText:"Cancel"}).click();
      await nativeRoute(panel, "assistant/voice");
      await panel.locator('[data-config="voice_scope_policy"]').selectOption("device_mapping");
      await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("default_user");
      const picker = panel.locator("#config-voice_default_user_picker");
      await expect.poll(() => picker.evaluate((element, id) => element.users?.some(user => user.id === id), process.env.REAL_HA_SMOKE_USER_ID)).toBe(true);
      await picker.locator("ha-picker-field").click();
      await picker.locator("ha-combo-box-item").filter({hasText:process.env.REAL_HA_PICKER_USER_NAME}).locator("button").click();
      await expect(picker).toHaveJSProperty("value", process.env.REAL_HA_SMOKE_USER_ID);
      await panel.locator("#revert-config").click();
      await nativeRoute(panel, "assistant/prompt-context");
      await panel.locator('[data-config="exposed_entities_enabled"]').check();
      const entity = panel.locator("#exposed-entity-picker");
      await entity.locator("ha-picker-field").click();
      await entity.locator("ha-combo-box-item").filter({hasText:"sensor.cold_attribute_kitchen"}).locator("button").click();
      await expect(panel.locator("[data-exposed-editor]")).toContainText("sensor.cold_attribute_kitchen");
      await panel.locator('[data-exposed-attribute][data-attribute="battery_level"]').check();
      await panel.locator("[data-close-exposed-editor]").click();
      await panel.locator("#revert-config").click();
      await nativeRoute(panel, "assistant/basics");
      await panel.locator(".agent-actions-menu summary").click();
      await panel.locator("#import-agent").click();
      await panel.locator("#import-document").fill(`invalid cancelled import ${index}`);
      await panel.locator("#import-preview").click();
      await expect(panel.locator("#import-apply")).toBeDisabled();
      await panel.locator("#import-cancel").click();
      await nativeRoute(panel, "capabilities/functions");
      await expect(panel.locator("dialog[open]")).toHaveCount(0);
      await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
    };
    await cycle("warm");
    const documentIdentity = await page.evaluate(() => performance.timeOrigin);
    const session = await context.newCDPSession(page);
    const samples = [];
    try {
      for (let window = 0; window < 8; window++) {
        for (let iteration = 0; iteration < 3; iteration++) await cycle(`${window}-${iteration}`);
        samples.push(await sampleRetainedRuntime(session));
      }
      expect(await page.evaluate(() => performance.timeOrigin)).toBe(documentIdentity);
      expect(retentionGrowth(samples)).toEqual([]);
      expect(await page.evaluate(() => window.__nativeMutations)).toHaveLength(0);
      await panel.locator("#function-add").click();
      await panel.locator("#add-tool").click();
      await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), lifetimeYaml("One keyboard commit"));
      await page.keyboard.press("Control+s");
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      await expect(panel.locator('[data-tool-key="native_lifetime_tool"]')).toContainText("One keyboard commit");
      expect(await page.evaluate(() => window.__nativeMutations)).toHaveLength(1);
      expect(errors).toEqual([]);
      await nativeEvidence("retention", {native_retention_windows:samples.length, native_keyboard_commits:(await page.evaluate(() => window.__nativeMutations)).length});
    } finally {
      await testInfo.attach("native-retention-windows", {body:JSON.stringify({documentIdentity, samples, widget_cycles:25}), contentType:"application/json"});
      await session.detach();
    }
  });

  test("two assistants retain their own saved state through held completion and stale tab", async ({context, page}, testInfo) => {
    test.setTimeout(120000);
    const panel = await openColdHaRoute(context, page, "assistant/basics");
    const choices = await panel.locator("#agent option").evaluateAll(nodes => nodes.map(node => node.value));
    expect(choices).toHaveLength(2);
    const expected = new Map();
    const seed = Number(process.env.STRESS_SEED || 97000);
    for (let step = 0; step < 6; step++) {
      const identity = choices[(step + seed) % 2];
      await panel.locator("#agent").selectOption(identity);
      await expect(panel.locator("#agent")).toBeEnabled();
      const title = `Native owner ${identity.slice(-5)} step ${step}`;
      await panel.locator('[data-config="__title"]').fill(title);
      if (step === 2) {
        await panel.evaluate(host => {
          const original = host.hass.callWS.bind(host.hass);
          host.hass.callWS = async message => {
            const result = await original(message);
            if (message.section === "configuration" && message.action === "save" && !window.__commitHeld) {
              window.__commitHeld = true;
              await new Promise(resolve => { window.__releaseCommit = resolve; });
            }
            return result;
          };
        });
      }
      await panel.locator("#save-config").click();
      if (step === 2) {
        await expect.poll(() => page.evaluate(() => Boolean(window.__commitHeld))).toBe(true);
        await expect(panel.locator("#agent")).toBeDisabled();
        await page.evaluate(() => window.__releaseCommit());
      }
      await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
      expected.set(identity, title);
      await nativeRoute(panel, "assistant/voice");
      await expect(panel.locator("#config-voice_default_user_picker")).toBeAttached();
      await nativeRoute(panel, "assistant/basics");
      await expect(panel.locator('[data-config="__title"]')).toHaveValue(title);
    }
    const other = await context.newPage();
    try {
      const stale = await openColdHaRoute(context, other, "assistant/basics");
      const identity = await panel.locator("#agent").inputValue();
      await stale.locator("#agent").selectOption(identity);
      await expect(stale.locator('[data-config="__title"]')).toHaveValue(expected.get(identity));
      await stale.locator('[data-config="__title"]').fill("Stale native owner draft");
      const finalTitle = "Authoritative native owner after conflict";
      await panel.locator('[data-config="__title"]').fill(finalTitle);
      await panel.locator("#save-config").click();
      await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
      expected.set(identity, finalTitle);
      await stale.locator("#save-config").click();
      await expect(stale.locator("#toast")).toContainText("changed in another tab");
      await expect(stale.locator('[data-config="__title"]')).toHaveValue("Stale native owner draft");
      await expect(stale.locator("#save-config")).toBeEnabled();
      for (const [identity, title] of expected) {
        await panel.locator("#agent").selectOption(identity);
        await expect(panel.locator('[data-config="__title"]')).toHaveValue(title);
        const authoritative = await panel.evaluate(host => host._call("configuration", "get"));
        expect(authoritative.title).toBe(title);
      }
      const oldIdentity = await panel.locator("#agent").inputValue();
      await nativeRoute(panel, "capabilities/functions");
      await panel.evaluate(host => {
        const original = host.hass.callWS.bind(host.hass);
        host.hass.callWS = async message => {
          const result = await original(message);
          if (message.section === "tools" && message.action === "ha_catalog" && !window.__catalogHeld) {
            window.__catalogHeld = {entry:message.entry_id, subentry:message.subentry_id};
            await new Promise(resolve => { window.__releaseCatalog = resolve; });
          }
          return result;
        };
      });
      await panel.locator("#function-add").click();
      await panel.locator("#add-ha-tools").click();
      await expect.poll(() => page.evaluate(() => Boolean(window.__catalogHeld))).toBe(true);
      await panel.locator("dialog[data-ha-llm-tools-dialog] [data-cancel]").click();
      await panel.locator("#agent").selectOption(choices.find(value => value !== oldIdentity));
      await expect(panel.locator("#agent")).toBeEnabled();
      await page.evaluate(() => window.__releaseCatalog());
      await expect.poll(() => panel.evaluate(host => host._haCatalogLoad == null)).toBe(true);
      expect(await panel.evaluate((host, old) => host._haCatalogAgent === old, oldIdentity)).toBe(false);
      await panel.locator("#function-add").click();
      await panel.locator("#add-ha-tools").click();
      await expect(panel.locator("dialog[data-ha-llm-tools-dialog] [data-status]")).not.toContainText("Loading");
      await panel.locator("dialog[data-ha-llm-tools-dialog] [data-cancel]").click();
      await nativeEvidence("assistants", {native_assistant_states:expected.size, native_held_completions:2, native_revision_conflicts:1});
      await testInfo.attach("native-assistant-commits", {body:JSON.stringify({seed, expected:[...expected], held_completions:2, stale_conflicts:1}), contentType:"application/json"});
    } finally { await other.close(); }
  });
});

test.skip(!baseUrl || !authDataRaw, "requires the dedicated genuine Home Assistant frontend-shell harness");

async function authenticate(context) {
  const authData = JSON.parse(authDataRaw);
  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);
}

async function openAssistantFromOverview(page) {
  const confirmHttpSettings = page.getByRole("button", {name: "Confirm", exact: true});

  // Keep this acceptance seam deliberately narrow: enter the registered HA panel
  // root first, let Home Assistant instantiate the custom panel, then use the
  // integration's own navigation to reach Assistant/Basics.
  await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);

  const confirmationVisible = await confirmHttpSettings
    .waitFor({state: "visible", timeout: 2_000})
    .then(() => true)
    .catch(() => false);
  if (confirmationVisible) {
    await confirmHttpSettings.click();
    await expect(confirmHttpSettings).toHaveCount(0);
    await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
    await expect(page.locator("home-assistant")).toHaveCount(1);
  }

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await expect(panel.getByRole("heading", {name: "Extended OpenAI", exact: true})).toBeVisible({timeout: 30_000});

  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible({timeout: 30_000});
  return panel;
}

async function openFunctionsFromOverview(page) {
  const panel = await openAssistantFromOverview(page);
  await panel.getByRole("button", {name: "Capabilities", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/home-assistant$/);
  await panel.getByRole("button", {name: "Functions", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/functions$/);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  return panel;
}

test("cold native Guest and Request Rule editors receive HA context and discard cancelled YAML", async ({context,page}) => {
  await authenticate(context);
  const panel=await openAssistantFromOverview(page);
  // The pristine HA fixture emits unrelated startup websocket rejections.
  // Capture every error throughout the native interactions under test.
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await panel.evaluate(host=>host._navigate("capabilities","guest-mode"));
  await expect(panel.locator(".guest-intro")).toBeVisible();
  if (await panel.locator("#guest-review-converted").count()) await panel.locator("#guest-review-converted").click();
  expect(await panel.evaluate(host=>host.hass===host._hass)).toBe(true);
  const guestSelector=panel.locator("ha-selector[data-guest-key]").first();
  expect(await guestSelector.evaluate(selector=>Boolean(selector.hass?.locale&&selector.hass?.localize))).toBe(true);
  await panel.evaluate(host=>host._navigate("capabilities","request-rules"));
  await expect(panel.locator("#rule-add")).toBeVisible();
  const name="Cold native cancelled YAML regression";
  await panel.evaluate(async(host,name)=>{
    try {
      await host._call("request_rules","create",{revision:host._result.revision,rule:{name,phrases:["cold native test"],match_type:"equals",action_type:"model_routing",action:{model:"gpt-5-mini",scope:"request",continue_to_ai:true,success_response:"Updated"},conditions:[{condition:"template",value_template:"{{ false }}"}]}});
    } catch (error) { throw new Error(error.message || JSON.stringify(error)); }
    await host._loadSection(true);
  },name);
  const card=panel.locator(".request-rule-card").filter({hasText:name});
  await card.locator(".rule-edit").click();
  const condition=panel.locator("#rule-condition-host > ha-selector");
  const row=condition.locator("ha-automation-condition-row").first();
  await expect(row).toBeVisible();
  await expect.poll(()=>condition.evaluate(selector=>selector.hass.localize("ui.panel.config.automation.editor.conditions.add"))).toBe("Add condition");
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  const yaml=condition.locator("ha-yaml-editor");
  await expect(yaml).toBeVisible();
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("false");
  await yaml.locator(".cm-content").fill("condition: template\nvalue_template: '{{ true }}'");
  await expect.poll(()=>condition.evaluate(selector=>selector.value[0]?.value_template)).toBe("{{ true }}");
  await panel.locator("#rule-dialog .rule-close").filter({hasText:"Cancel"}).click();
  await card.locator(".rule-edit").click();
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("false");
  await yaml.locator(".cm-content").fill("condition: template\nvalue_template: '{{ true }}'");
  await expect.poll(()=>condition.evaluate(selector=>selector.value[0]?.value_template)).toBe("{{ true }}");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open",false);
  await card.locator(".rule-edit").click();
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("true");
  await panel.locator("#rule-dialog .rule-close").filter({hasText:"Cancel"}).click();
  expect(errors).toEqual([]);
});

test("latency readiness helper sees panel inside genuine HA shadow DOM", async ({context, page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/overview`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  await expect(page.locator("extended-openai-management-panel")).toHaveCount(1);

  const route = {name: "overview", path: "overview"};
  await waitForManagementRouteReady(page, route, 30_000);
  const state = await managementRouteState(page);
  expect(state).toMatchObject({
    page: "overview",
    subsection: null,
    busy: false,
    error: null,
    loading: false,
  });
  expect(String(state?.renderedRoute || "")).toContain("|overview");
});

test("shipped management panel loads and persists one configuration change inside the genuine HA frontend", async ({context, page}) => {
  const integrationPageErrors = [];
  const integrationConsoleErrors = [];
  const integrationRequestFailures = [];
  const integrationResponses = [];

  page.on("pageerror", (error) => {
    const detail = [error.name, error.message, error.stack].filter(Boolean).join("\n");
    if (detail.includes("/extended_openai_conversation_responses/")) {
      integrationPageErrors.push(detail);
    }
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const location = message.location();
    const detail = `${message.text()}${location?.url ? ` (${location.url}:${location.lineNumber ?? 0})` : ""}`;
    if (
      location?.url?.includes("/extended_openai_conversation_responses/")
      || detail.includes("extended_openai_conversation_responses")
      || detail.includes("extended-openai-management-panel")
    ) {
      integrationConsoleErrors.push(detail);
    }
  });
  page.on("requestfailed", (request) => {
    if (request.url().includes("/extended_openai_conversation_responses/")) {
      integrationRequestFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "request failed"}`);
    }
  });
  page.on("response", (response) => {
    if (response.url().includes("/extended_openai_conversation_responses/")) {
      integrationResponses.push({url: response.url(), status: response.status()});
    }
  });

  await authenticate(context);

  // Genuine HA owns authentication, routing, custom-panel registration, and the
  // websocket. This test proves only that integration boundary plus one real save.
  let panel = await openAssistantFromOverview(page);
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();

  const title = panel.locator('[data-config="__title"]');
  await title.fill("Real HA shell saved");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  // Re-enter through HA's registered panel root instead of relying on a deep-route
  // reload. Persistence is still proved through a fresh panel lifecycle.
  panel = await openAssistantFromOverview(page);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Real HA shell saved");
  await expect(panel.locator("#agent option:checked")).toHaveText("Real HA shell saved");

  expect(integrationRequestFailures).toEqual([]);
  expect(integrationPageErrors).toEqual([]);
  expect(integrationConsoleErrors).toEqual([]);
  expect(integrationResponses.some(({url, status}) => {
    const path = new URL(url).pathname;
    return /^\/extended_openai_conversation_responses\/frontend\/assets\/management-[A-Za-z0-9_-]+\.js$/.test(path)
      && status === 200;
  })).toBe(true);
  expect(integrationResponses.filter(({status}) => status >= 400)).toEqual([]);
});

test("genuine Home Assistant shell follows deep links and browser history", async ({context, page}) => {
  const authData = JSON.parse(authDataRaw);
  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);

  // Start on a nested deep link, not the panel's normal landing page.
  await page.goto(`${baseUrl}/extended-openai/data-memory/knowledge`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/data-memory/knowledge`);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  // Build history through the shipped panel's own navigation handlers.
  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await panel.getByRole("button", {name: "Capabilities", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await panel.getByRole("button", {name: "Functions", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/functions`);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();

  // Home Assistant owns popstate handling. The custom panel must follow the URL
  // backward and forward instead of leaving stale content from the previous view.
  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/data-memory/knowledge`);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/functions`);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
});

test("genuine HA native YAML editor saves with Ctrl+S and survives a fresh panel lifecycle", async ({context, page}) => {
  await authenticate(context);
  let panel = await openFunctionsFromOverview(page);

  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  const nativeEditor = panel.locator("#tool-yaml-native");
  const fallback = panel.locator("#tool-yaml");
  await expect(nativeEditor).toBeVisible({timeout: 30_000});
  await expect(fallback).toBeHidden();
  await expect(nativeEditor).toHaveJSProperty("tagName", "HA-YAML-EDITOR");
  expect(await nativeEditor.evaluate(() => Boolean(customElements.get("ha-yaml-editor")))).toBe(true);

  const initialTool = {
    spec: {
      name: "real_shell_native_tool",
      description: "Genuine HA native YAML editor",
      parameters: {type: "object", properties: {}},
    },
    function: {type: "native", name: "get_user_from_user_id"},
  };
  await nativeEditor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, initialTool);
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");

  // Exercise keyboard reachability through the real HA component before using its
  // native save shortcut. Hidden fallback controls must not trap focus.
  await panel.locator("#built-in-function").focus();
  await page.keyboard.press("Tab");
  await expect.poll(() => nativeEditor.evaluate((element) => element.matches(":focus-within"))).toBe(true);
  expect(await nativeEditor.evaluate((element) => Boolean(element.shadowRoot?.activeElement))).toBe(true);
  await page.keyboard.press("Control+s");

  let card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor");
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"})).toHaveCount(1);

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor");
  await card.locator(".edit-tool").click();
  const reopenedEditor = panel.locator("#tool-yaml-native");
  await expect(reopenedEditor).toBeVisible();
  await expect.poll(() => reopenedEditor.evaluate((element) => element.yaml)).toContain("real_shell_native_tool");

  const editedTool = structuredClone(initialTool);
  editedTool.spec.description = "Genuine HA native YAML editor edited";
  await reopenedEditor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, editedTool);
  await panel.locator("#tool-validate").click();
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
  await expect(panel.locator("#tool-error")).toContainText("Name: real_shell_native_tool");
  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  await expect(card).toContainText("Genuine HA native YAML editor edited");

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor edited");

  // Prove the panel remains healthy outside Function Tools after native-editor use.
  await panel.getByRole("button", {name: "Data & Memory", exact: true}).click();
  await panel.getByRole("button", {name: "Knowledge Library", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/data-memory\/knowledge$/);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  // Clean up the acceptance tool so this test remains friendly to retries.
  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await card.locator(".delete-tool").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"})).toHaveCount(0);
});

test("long native Function YAML scrolls inside the editor while dialog actions stay visible", async ({context, page}) => {
  await authenticate(context);
  const panel = await openFunctionsFromOverview(page);
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const dialog = panel.locator("#tool-dialog");
  const editor = panel.locator("#tool-yaml-native");
  await expect(editor).toBeVisible({timeout: 30_000});

  const properties = Object.fromEntries(Array.from({length: 90}, (_, index) => [
    `long_field_${String(index).padStart(3, "0")}`,
    {type: "string", description: `A long YAML editor scroll fixture field ${index}`},
  ]));
  const longTool = {
    spec: {
      name: "real_shell_long_yaml_scroll_tool",
      description: "Long YAML editor scroll fixture",
      parameters: {type: "object", properties},
    },
    function: {type: "native", name: "get_user_from_user_id"},
  };
  await editor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, longTool);
  await expect.poll(() => editor.evaluate((element) => element.yaml.split("\n").length)).toBeGreaterThan(200);

  const actions = dialog.locator(".dialog-actions");
  const initialLayout = await dialog.evaluate((element) => {
    const rect = (node) => {
      const {top, bottom} = node.getBoundingClientRect();
      return {top, bottom};
    };
    return {dialog: rect(element), actions: rect(element.querySelector(".dialog-actions"))};
  });
  expect(initialLayout.dialog.top).toBeGreaterThanOrEqual(0);
  expect(initialLayout.dialog.bottom).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  expect(initialLayout.actions.bottom).toBeLessThanOrEqual(initialLayout.dialog.bottom);
  await expect(actions.getByRole("button", {name: "Save", exact: true})).toBeVisible();

  const scrollState = () => editor.evaluate((host) => {
    const scrollables = [];
    const visit = (root) => {
      for (const element of root.querySelectorAll("*")) {
        const style = getComputedStyle(element);
        if (element.scrollHeight > element.clientHeight + 2 && /auto|scroll/.test(style.overflowY)) {
          scrollables.push(element);
        }
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    if (host.scrollHeight > host.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(host).overflowY)) {
      scrollables.push(host);
    }
    if (host.shadowRoot) visit(host.shadowRoot);
    return {
      count: scrollables.length,
      top: Math.max(0, ...scrollables.map((element) => element.scrollTop)),
    };
  });
  await expect.poll(async () => (await scrollState()).count).toBeGreaterThan(0);
  const editorRect = await editor.boundingBox();
  expect(editorRect).not.toBeNull();
  await page.mouse.move(editorRect.x + editorRect.width / 2, editorRect.y + editorRect.height / 2);
  await page.mouse.wheel(0, 700);
  await page.waitForTimeout(300);
  await expect.poll(async () => (await scrollState()).top).toBeGreaterThan(0);

  const finalActions = await actions.boundingBox();
  expect(finalActions).not.toBeNull();
  expect(finalActions.y).toBeCloseTo(initialLayout.actions.top, 0);
  expect(finalActions.y + finalActions.height).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  await expect(actions.getByRole("button", {name: "Save", exact: true})).toBeVisible();
  await panel.locator("#tool-cancel").click();
  await expect(dialog).toHaveJSProperty("open", false);
});

test("genuine HA YAML keyboard edits validate before one persisted save", async ({context, page}) => {
  await authenticate(context);
  let panel = await openFunctionsFromOverview(page);
  await panel.evaluate((element) => {
    window.__keyboardToolSaves = [];
    const original = element._hass.callWS.bind(element._hass);
    element._hass.callWS = async (message) => {
      if (message.section === "tools" && message.action === "save") {
        window.__keyboardToolSaves.push(structuredClone(message));
      }
      return original(message);
    };
  });
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const dialog = panel.locator("#tool-dialog");
  const editor = panel.locator("#tool-yaml-native");
  await expect(editor).toBeVisible({timeout: 30_000});
  const surface = editor.locator('[contenteditable="true"], textarea').first();
  await expect(surface).toBeVisible();

  const yaml = (description) => `spec:\n  name: real_shell_keyboard_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: get_user_from_user_id\n`;
  const replaceThroughKeyboard = async (value) => {
    await surface.click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(value);
    await expect.poll(() => editor.evaluate((element) => element.yaml)).toBe(value);
  };

  await replaceThroughKeyboard(yaml("First valid keyboard edit"));
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");
  await replaceThroughKeyboard(yaml("[unterminated"));
  await expect(panel.locator("#tool-error")).toHaveClass(/invalid/);
  await panel.locator("#tool-save").click();
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(panel.locator("#tool-error")).toHaveClass(/invalid/);
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"})).toHaveCount(0);
  expect(await page.evaluate(() => window.__keyboardToolSaves)).toHaveLength(0);

  await replaceThroughKeyboard(yaml("Final corrected keyboard edit"));
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");
  await panel.locator("#tool-save").click();
  await expect(dialog).toHaveJSProperty("open", false);
  let card = panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"});
  await expect(card).toContainText("Final corrected keyboard edit");
  await expect(card).not.toContainText("First valid keyboard edit");
  expect(await page.evaluate(() => window.__keyboardToolSaves)).toHaveLength(1);

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"});
  await expect(card).toContainText("Final corrected keyboard edit");
  await card.locator(".delete-tool").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await expect(card).toHaveCount(0);
});

test("cold Voice user pickers select a secondary HA user and keep its name after save/reload", async ({context,page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/assistant/voice`, {waitUntil:"domcontentloaded"});
  const panel=page.locator("extended-openai-management-panel");
  const defaultPicker=panel.locator('#config-voice_default_user_picker');
  await expect(defaultPicker.locator('ha-generic-picker')).toHaveCount(1);
  const secondaryName="Cold Voice secondary user";
  const secondaryId=await panel.evaluate(async(host,name)=>{
    let users=await host.hass.callWS({type:"config/auth/list"});
    if(!users.some(user=>user.name===name)) {
      await host.hass.callWS({type:"config/auth/create",name});
      users=await host.hass.callWS({type:"config/auth/list"});
    }
    const user=users.find(user=>user.name===name);
    // Seed an unavailable device to exercise saved ownership without hardware.
    host._draft.voice_default_user_id="";
    host._draft.voice_device_mappings={"cold-voice-regression-device":"unretained"};
    host._configDirty=true;
    host._render();
    return user.id;
  },secondaryName);
  await panel.locator('[data-config="voice_scope_policy"]').selectOption('device_mapping');
  await panel.locator('[data-config="voice_unmapped_policy"]').selectOption('default_user');
  // The user was created after the initial catalogue fetch; a fresh cold reload
  // verifies native registration and obtains the current HA users.
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await expect(defaultPicker.locator('ha-generic-picker')).toHaveCount(1);
  await defaultPicker.locator('ha-picker-field').click();
  await defaultPicker.locator('ha-picker-combo-box ha-combo-box-item').filter({hasText:secondaryName}).locator('button').click();
  await expect(defaultPicker).toHaveJSProperty('value',secondaryId);
  const mappingPicker=panel.locator('.voice-owner-user-picker');
  await panel.locator('.voice-owner-type').selectOption('user');
  await expect(mappingPicker.locator('ha-generic-picker')).toHaveCount(1);
  await mappingPicker.locator('ha-picker-field').click();
  await mappingPicker.locator('ha-picker-combo-box ha-combo-box-item').filter({hasText:secondaryName}).locator('button').click();
  await expect(mappingPicker).toHaveJSProperty('value',secondaryId);
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await expect(defaultPicker).toHaveJSProperty('value',secondaryId);
  await expect(mappingPicker).toHaveJSProperty('value',secondaryId);
  await expect(defaultPicker).toContainText(secondaryName);
  await expect(mappingPicker).toContainText(secondaryName);
  await expect(panel.locator('#voice-current-summary')).toContainText(secondaryName);
  await expect(defaultPicker).not.toContainText('Unknown user selected');
  await expect(mappingPicker).not.toContainText('Unknown user selected');
  const saved=await panel.evaluate(host=>host._draft.voice_device_mappings);
  expect(saved['cold-voice-regression-device']).toBe(`user:${secondaryId}`);
});

test("cold Prompt entity editor switches immediately and saves through the native HA shell", async ({context,page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/assistant/prompt-context`,{waitUntil:"domcontentloaded"});
  const panel=page.locator('extended-openai-management-panel');
  await panel.locator('#exposed-entity-picker').waitFor({state:"attached"});
  await panel.locator('[data-config="exposed_entities_enabled"]').check();
  const select=async entityId=>{
    const picker=panel.locator('#exposed-entity-picker');
    await picker.evaluate((element,value)=>{element.value=value;element.dispatchEvent(new CustomEvent('value-changed',{detail:{value},bubbles:true}));},entityId);
  };
  await select('sensor.cold_attribute_kitchen');
  await expect(panel.locator('[data-exposed-editor]')).toContainText('sensor.cold_attribute_kitchen');
  await select('sensor.cold_attribute_hall');
  await expect(panel.locator('[data-exposed-editor]')).toContainText('sensor.cold_attribute_hall');
  await panel.locator('[data-exposed-attribute][data-attribute="battery_level"]').check();
  await panel.locator('[data-close-exposed-editor]').click();
  await expect(panel.locator('[data-exposed-editor]')).toHaveCount(0);
  await panel.locator('[data-edit-exposed-entity="sensor.cold_attribute_hall"]').click();
  await expect(panel.locator('[data-exposed-attribute][data-attribute="battery_level"]')).toBeChecked();
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await panel.locator('[data-edit-exposed-entity="sensor.cold_attribute_hall"]').click();
  await expect(panel.locator('[data-exposed-attribute][data-attribute="battery_level"]')).toBeChecked();
});
