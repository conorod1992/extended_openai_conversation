import {expect, test} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {acceptSharingReview, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const switchBackend = process.env.REAL_HA_IDENTITY_SWITCH_BACKEND;
const switchControl = process.env.REAL_HA_IDENTITY_SWITCH_CONTROL;
const permissionBackend = process.env.REAL_HA_PERMISSION_BACKEND;
const permissionControl = process.env.REAL_HA_PERMISSION_CONTROL;
const raceBackendA = process.env.REAL_HA_ADMIN_RACE_BACKEND_A;
const raceBackendB = process.env.REAL_HA_ADMIN_RACE_BACKEND_B;

const fixture = (backend, route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backend)}`;

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    body: JSON.stringify(body),
  });
  expect(response.ok).toBeTruthy();
  return response.json();
}

async function checkConfigurationExportRetirement(page, bundled) {
  const marker = `Auth-owned configuration export ${bundled ? "bundle" : "source"}`;
  await page.goto(`${fixture(raceBackendA, "assistant/basics")}&bundle=${bundled ? "1" : "0"}`);
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#export-agent")).toBeAttached();
  const owner = await panel.evaluate(async host => {
    const user = await host.hass.callWS({type:"auth/current_user"});
    host.hass = {...host.hass, user};
    return user;
  });
  expect(owner.is_admin).toBe(true);
  await panel.evaluate(async (host, marker) => {
    const saved = await host._call("configuration", "get");
    await host._call("configuration", "save", {revision:saved.revision, config:{prompt:marker}});
  }, marker);

  const startExport = async () => {
    if (!await panel.locator("#export-agent").isVisible()) await panel.locator(".agent-actions-menu > summary").click();
    await panel.locator("#export-agent").click();
    await panel.locator("[data-custom]").click();
  };
  const downloadExport = async () => {
    await startExport();
    await expect(panel.locator("[data-artifact]")).toHaveValue(new RegExp(marker));
    const pending = page.waitForEvent("download");
    await acceptSharingReview(panel);
    expect(await readFile(await (await pending).path(), "utf8")).toContain(marker);
  };
  await downloadExport(); // Healthy unchanged-owner baseline through the real UI.

  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    window.pendingConfigurationExport = {generation:host._eocAuthenticationGeneration, published:[]};
    const operation = pendingConfigurationExport;
    operation.observer = new MutationObserver(() => {
      const artifact = host.shadowRoot.querySelector("[data-artifact]");
      if (artifact) operation.published.push({user:host.hass.user.id, value:artifact.value});
    });
    operation.observer.observe(host.shadowRoot, {childList:true, subtree:true});
    host.hass.callWS = async message => {
      const result = await call(message);
      if (message.section !== "configuration" || message.action !== "export") return result;
      operation.ready = true;
      return new Promise(resolve => { operation.release = () => resolve(result); });
    };
  });
  await startExport();
  await expect.poll(() => page.evaluate(() => pendingConfigurationExport.ready)).toBe(true);
  const replacement = await panel.evaluate(async (host, backend) => {
    const callWS = async message => {
      const response = await fetch(backend, {method:"POST", body:JSON.stringify(message)});
      const result = await response.json();
      if (!response.ok) throw Error(result.message || "Authenticated HA request failed");
      return result;
    };
    const user = await callWS({type:"auth/current_user"});
    host.hass = {...host.hass, user, callWS};
    return user;
  }, raceBackendB);
  expect(replacement.id).not.toBe(owner.id);
  expect(replacement.is_admin).toBe(true);
  await expect(panel.getByRole("button", {name:"Reload settings", exact:true})).toBeVisible();
  const retiredDownloads = [];
  const onDownload = download => retiredDownloads.push(download);
  page.on("download", onDownload);
  await page.evaluate(async () => {
    pendingConfigurationExport.release();
    // The held response's handler drains its microtasks before the next task.
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  expect(await panel.evaluate(host => host.hass.user.id)).toBe(replacement.id);
  expect(await panel.evaluate(host => host._eocAuthenticationGeneration)).toBeGreaterThan(await page.evaluate(() => pendingConfigurationExport.generation));
  await expect(panel.locator("[data-artifact]")).toHaveCount(0);
  expect(await page.evaluate(() => pendingConfigurationExport.published)).toEqual([]);
  expect(retiredDownloads).toHaveLength(0);
  page.off("download", onDownload);
  await page.evaluate(() => pendingConfigurationExport.observer.disconnect());

  // The retired panel remains owned by A. Open a fresh authoring session for B
  // with B's genuine connection, without reviving A's pending operation.
  await panel.evaluate(host => {
    const fresh = document.createElement("extended-openai-management-panel");
    fresh.hass = host.hass;
    host.replaceWith(fresh);
    browserHarness.panel = fresh;
  });
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#export-agent")).toBeAttached();
  await downloadExport();
}

async function checkBackupCleanupTransport(page, bundled) {
  const cases = [
    {mode:"custom", admin:false},
    {mode:"custom", admin:true},
    {mode:"full", admin:false},
    {mode:"full", admin:true, failure:true},
    {mode:"full", admin:true, stage:"export_start"},
  ];
  try {
    for (const scenario of cases) await test.step(JSON.stringify(scenario), async () => {
      await post(permissionControl, {admin:scenario.admin});
      await page.goto(`${fixture(raceBackendA, "usage-maintenance/backup-restore")}&bundle=${bundled ? "1" : "0"}`);
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel.locator("#create-backup-transfer")).toBeVisible();
      await panel.evaluate(async (host, scenario) => {
        const user = await host.hass.callWS({type:"auth/current_user"});
        host.hass = {...host.hass, user};
        const original = host.hass.callWS.bind(host.hass);
        window.backupCleanup = {ownerId:user.id, calls:[], cancellations:[], replacementCalls:[], button:host.shadowRoot.querySelector("#create-backup-transfer")};
        host.hass.callWS = async message => {
          const result = await original(message);
          if (!message.type?.endsWith("/backup_transfer")) return result;
          backupCleanup.calls.push(message.action);
          if (message.action === "export_cancel") backupCleanup.cancellations.push(result);
          if (message.action !== (scenario.stage || "export_chunk")) return result;
          backupCleanup.sessionId = result.session_id;
          backupCleanup.ready = true;
          return new Promise((resolve, reject) => {
            backupCleanup.release = () => scenario.failure ? reject(Error("controlled chunk completion error")) : resolve(result);
          });
        };
      }, scenario);
      await panel.locator("#transfer-export-mode").selectOption(scenario.mode);
      await panel.locator("#create-backup-transfer").click();
      await expect.poll(() => page.evaluate(() => backupCleanup.ready)).toBe(true);
      await panel.evaluate(async (host, backend) => {
        const callWS = async message => {
          const response = await fetch(backend, {method:"POST", body:JSON.stringify(message)});
          const result = await response.json();
          if (!response.ok) throw Error(result.message || "Authenticated HA request failed");
          return result;
        };
        const user = await callWS({type:"auth/current_user"});
        backupCleanup.replacementUser = user;
        host.hass = {...host.hass, user, callWS:message => {
          backupCleanup.replacementCalls.push(message);
          return callWS(message);
        }};
      }, permissionBackend);
      expect(await page.evaluate(() => backupCleanup.replacementUser.id)).not.toBe(await page.evaluate(() => backupCleanup.ownerId));
      expect(await page.evaluate(() => backupCleanup.replacementUser.is_admin)).toBe(scenario.admin);
      await expect(panel.getByRole("button", {name:"Reload settings", exact:true})).toBeVisible();
      const downloads = [];
      const onDownload = download => downloads.push(download);
      page.on("download", onDownload);
      try {
        await page.evaluate(() => backupCleanup.release());
        await expect.poll(() => page.evaluate(() => backupCleanup.button.disabled)).toBe(false);
        expect(downloads).toHaveLength(0);
        expect(await page.evaluate(() => backupCleanup.replacementCalls)).toEqual([]);
        expect(await page.evaluate(() => backupCleanup.cancellations)).toEqual([{cancelled:true}]);
        expect(await page.evaluate(() => backupCleanup.calls)).toEqual(scenario.stage === "export_start"
          ? ["export_start", "export_cancel"] : ["export_start", "export_chunk", "export_cancel"]);
        expect(await panel.evaluate(host => host.hass.user.id)).toBe(await page.evaluate(() => backupCleanup.replacementUser.id));
        await expect(panel.locator("[data-artifact]")).toHaveCount(0);
      } finally {
        page.off("download", onDownload);
      }
    });
  } finally {
    await post(permissionControl, {admin:true});
  }
}

test("one browser profile does not carry private EOAI state from user A into user B", async ({page}) => {
  test.skip(!switchBackend || !switchControl || !raceBackendA || !raceBackendB || !permissionBackend || !permissionControl, "requires switchable and separately authenticated HA identities");
  test.setTimeout(120_000);
  const errors = trackPageErrors(page);

  await page.goto(fixture(switchBackend, "data-memory/memories"));
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Private same-browser marker A");
  await panel.locator("#memory-category").fill("identity-a");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toBeVisible();

  // Leave a browser-local draft open as well as committed private data.
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Unsaved private draft A");
  await page.evaluate(url => postMessage({noop:true}, "*"));
  await post(switchControl, {identity: "b"});

  // Same page/context/profile, but a fresh HA-authenticated identity.
  await page.goto(fixture(switchBackend, "data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toHaveCount(0);
  await expect(panel.locator("#memory-dialog")).not.toBeVisible();
  await expect(panel.getByText("Unsaved private draft A", {exact: true})).toHaveCount(0);

  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Private same-browser marker B");
  await panel.locator("#memory-category").fill("identity-b");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Private same-browser marker B", {exact: true})).toBeVisible();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toHaveCount(0);
  for (const bundled of [false, true]) await test.step(`configuration export ownership (${bundled ? "bundle" : "source"})`, () => checkConfigurationExportRetirement(page, bundled));
  for (const bundled of [false, true]) await test.step(`backup cleanup ownership (${bundled ? "bundle" : "source"})`, () => checkBackupCleanupTransport(page, bundled));
  await expectHarnessClean(page, errors);
});

test("an already-authenticated browser obeys live HA admin downgrade and restoration", async ({page}) => {
  test.skip(!permissionBackend || !permissionControl, "requires mutable genuine HA user permissions");
  test.setTimeout(120_000);
  const errors = trackPageErrors(page);

  await page.goto(fixture(permissionBackend, "assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  const request = await page.evaluate(() => {
    const call = browserHarness.calls.find(item => item.section === "configuration" && item.action === "get");
    return {
      type: "extended_openai_conversation_responses/management",
      entry_id: call.entry_id,
      subentry_id: call.subentry_id,
      section: "configuration",
      action: "get",
    };
  });

  await post(permissionControl, {admin: false});
  const denied = await page.evaluate(async message => {
    try {
      await browserHarness.hass.callWS(message);
      return {ok: true};
    } catch (error) {
      return {ok: false, message: String(error?.message || error)};
    }
  }, request);
  expect(denied.ok).toBe(false);
  expect(denied.message).toMatch(/administrator permission is required/i);

  await post(permissionControl, {admin: true});
  const restored = await page.evaluate(async message => {
    try {
      const result = await browserHarness.hass.callWS(message);
      return {ok: true, title: result?.config?.__title || result?.config?.title || ""};
    } catch (error) {
      return {ok: false, message: String(error?.message || error)};
    }
  }, request);
  expect(restored.ok).toBe(true);
  expect(errors).toHaveLength(0);
  await expect.poll(() => errors.consoleErrors).toEqual([
    `Failed to load resource: the server responded with a status of 400 (Bad Request) (${permissionBackend}?client=default:0)`,
  ]);
  expect(errors.requestFailures).toEqual([]);
  expect(errors.badResponses).toEqual([`400 POST ${permissionBackend}?client=default`]);
  expect(await page.evaluate(() => ({
    errors: browserHarness.windowErrors,
    rejections: browserHarness.rejections,
  }))).toEqual({errors: [], rejections: []});
});

test("two separately authenticated admins cannot overwrite the same stale Request Rule", async ({browser}) => {
  test.skip(!raceBackendA || !raceBackendB, "requires two independent genuine HA admin sessions");
  test.setTimeout(120_000);

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const errorsA = trackPageErrors(pageA);
  const errorsB = trackPageErrors(pageB);

  try {
    await pageA.goto(fixture(raceBackendA, "capabilities/request-rules"));
    const panelA = pageA.locator("extended-openai-management-panel");
    await panelA.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await panelA.locator("#rule-name").fill("Cross-admin shared rule");
    await panelA.locator("#rule-phrases").fill("cross admin phrase");
    await panelA.locator("#rule-action-type").selectOption("model_routing");
    await panelA.locator("#rule-model").fill("gpt-5-mini");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "Cross-admin shared rule", exact: true})).toBeVisible();

    await pageB.goto(fixture(raceBackendB, "capabilities/request-rules"));
    const panelB = pageB.locator("extended-openai-management-panel");
    const cardA = panelA.locator(".request-rule-card").filter({hasText: "Cross-admin shared rule"});
    const cardB = panelB.locator(".request-rule-card").filter({hasText: "Cross-admin shared rule"});
    await expect(cardB).toBeVisible();
    await cardA.locator(".rule-edit").click();
    await cardB.locator(".rule-edit").click();

    await panelA.locator("#rule-name").fill("Cross-admin authoritative A");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "Cross-admin authoritative A", exact: true})).toBeVisible();

    await panelB.locator("#rule-name").fill("Cross-admin stale B");
    await panelB.locator("#rule-save").click();
    await expect(panelB.getByText(/changed in another tab/i).first()).toBeVisible();
    await expect(panelB.locator("#rule-name")).toHaveValue("Cross-admin stale B");

    await pageB.goto(fixture(raceBackendB, "capabilities/request-rules"));
    await expect(panelB.getByRole("heading", {name: "Cross-admin authoritative A", exact: true})).toBeVisible();
    await expect(panelB.getByRole("heading", {name: "Cross-admin stale B", exact: true})).toHaveCount(0);
    await expectHarnessClean(pageA, errorsA);
    expect(errorsB).toHaveLength(0);
    await expect.poll(() => errorsB.consoleErrors).toEqual([
      `Failed to load resource: the server responded with a status of 400 (Bad Request) (${raceBackendB}?client=default:0)`,
    ]);
    expect(errorsB.requestFailures).toEqual([]);
    expect(errorsB.badResponses).toEqual([`400 POST ${raceBackendB}?client=default`]);
    expect(await pageB.evaluate(() => ({
      errors: browserHarness.windowErrors,
      rejections: browserHarness.rejections,
    }))).toEqual({errors: [], rejections: []});
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
