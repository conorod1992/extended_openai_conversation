import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const repairedTool = name => ({
  spec:{name, description:`Corrected ${name}`, parameters:{type:"object", properties:{
    message:{type:"string", minLength:1},
  }}},
  function:{type:"template", value_template:"{{ message | default('Hello') }}"},
});
const repairedYaml = name => JSON.stringify(repairedTool(name), null, 2);

async function prepareRepair(page, bundled, hold = null) {
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  await panel.evaluate((host, hold) => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    // A recoverable legacy parameter-schema error quarantines these two tools;
    // the baseline fixture's healthy Function and Group remain usable.
    const invalid = name => ({
      spec:{name, description:`Legacy ${name}`, parameters:{type:"object", properties:{
        message:{type:"string", minLength:"1"},
      }}},
      function:{type:"template", value_template:"{{ message | default('Hello') }}"},
    });
    const baseline = structuredClone(host._configData);
    const healthy = structuredClone(baseline.config.functions);
    const raw = [invalid("repair_a"), invalid("repair_b"), ...healthy];
    const state = {
      raw, revision:baseline.revision, validations:[], saves:[], normalSaves:[],
      validationEntered:false, saveEntered:false, validationReturned:false, saveReturned:false,
    };
    window.repairOwnership = state;
    const error = "minLength must be a non-negative integer";
    const snapshot = () => {
      const invalid_tools = raw.flatMap((tool, index) =>
        typeof tool.spec.parameters.properties?.message?.minLength === "string"
          ? [{index, name:tool.spec.name, tool:structuredClone(tool),
            yaml:JSON.stringify(tool, null, 2), validation_error:error}] : []);
      const invalidIndexes = new Set(invalid_tools.map(item => item.index));
      return {...structuredClone(baseline), revision:state.revision,
        config:{...structuredClone(baseline.config), functions:raw.filter((_, index) => !invalidIndexes.has(index)).map(tool => structuredClone(tool))},
        function_repair:{invalid_tools, invalid_count:invalid_tools.length, group_issues:[],
          persisted_groups:structuredClone(baseline.config.function_groups), tools_valid:invalid_tools.length === 0,
          isolatable:invalid_tools.length > 0, validation_error:invalid_tools.length ? error : null,
          tools:invalid_tools.length ? null : structuredClone(raw)},
      };
    };
    state.snapshot = snapshot;
    const issue = {field:"functions", repairable:true, message:error};
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      if (message.section === "tools" && message.action === "validate_yaml") {
        state.validations.push(structuredClone(message));
        state.validationEntered = true;
        if (hold === "validation" && state.validations.length === 1) await new Promise(resolve => {state.release = resolve;});
        const config = JSON.parse(message.yaml);
        if (typeof config.spec.parameters.properties.message.minLength !== "number") {
          throw new Error("The test must submit a corrected Function schema");
        }
        state.validationReturned = true;
        return {valid:true, config, name:config.spec.name, type:config.function.type};
      }
      if (message.section === "function_repair" && message.action === "save_one") {
        state.saves.push(structuredClone(message));
        state.saveEntered = true;
        if (hold === "save" && state.saves.length === 1) await new Promise(resolve => {state.release = resolve;});
        if (message.revision !== state.revision) throw new Error("Saved repair changed; reload before saving");
        if (!Number.isInteger(message.index) || !raw[message.index]) throw new Error("Invalid repair target");
        raw[message.index] = structuredClone(message.tool);
        state.revision = `repair-saved-${state.saves.length}`;
        state.saveReturned = true;
        const saved = snapshot();
        const agent = {...host._selectedAgent()};
        if (saved.function_repair.invalid_count) agent.configuration_issue = issue;
        else delete agent.configuration_issue;
        return {valid:true, ...saved, agent};
      }
      if (message.section === "tools" && message.action === "save") {
        state.normalSaves.push(structuredClone(message));
        if (message.revision !== state.revision) throw new Error("Saved repair changed; reload before saving");
        const index = raw.findIndex(tool => tool.spec.name === message.original_name);
        if (index < 0) throw new Error("An acknowledged repair must update its persisted tool");
        raw[index] = JSON.parse(message.yaml);
        state.revision = `repair-saved-${state.saves.length + state.normalSaves.length}`;
        const saved = snapshot();
        return {functions:saved.config.functions, function_groups:saved.config.function_groups,
          function_repair:saved.function_repair, revision:saved.revision};
      }
      if ((message.section === "configuration" && message.action === "get")
          || (message.section === "function_repair" && message.action === "configuration_get")) return snapshot();
      const response = await original(message);
      if (!message.section && message.action === "agents") {
        for (const agent of response.agents) if (agent.subentry_id === host._agentId) {
          agent.configuration_issue = structuredClone(issue);
          agent.function_count = snapshot().config.functions.length;
        }
      }
      return response;
    };
    host._configData = snapshot();
    host._result = host._configData;
    host._selectedAgent().configuration_issue = issue;
    host._render();
  }, hold);
  await expect(panel.locator(".edit-invalid-tool")).toHaveCount(2);
  return panel;
}

async function openRepair(panel, index) {
  await panel.locator(`.edit-invalid-tool[data-repair-index="${index}"]`).click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
}

async function settleReleasedOperation(page) {
  await page.evaluate(async () => {
    window.repairOwnership.release();
    // The controlled response resolves into the real event handler. Two frames
    // drain that handler's microtasks without relying on a wall-clock sleep.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

async function prepareHealthyOperation(page, bundled, hold) {
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await panel.getByText("Show member functions", {exact:true}).click();
  await panel.locator(".edit-tool").first().click();
  await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
  await panel.evaluate((host, hold) => {
    const original = host.hass.callWS.bind(host.hass);
    const state = {saves:[], validations:[], entered:false};
    window.repairOwnership = state;
    host.hass.callWS = async message => {
      if (message.section === "tools" && ["save", "validate_yaml"].includes(message.action)) {
        const calls = message.action === "save" ? state.saves : state.validations;
        calls.push(structuredClone(message));
        if (calls.length === 1 && message.action === hold) {
          state.entered = true;
          await new Promise(resolve => {state.release = resolve;});
        }
      }
      return original(message);
    };
  }, hold);
  return panel;
}

function toolDraft(repair, description) {
  if (repair) return JSON.stringify({...repairedTool("repair_a"), spec:{...repairedTool("repair_a").spec, description}}, null, 2);
  return `spec:\n  name: baseline_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n`;
}

for (const bundled of [false, true]) {
  const version = bundled ? "bundle" : "source";

  for (const group of [false, true]) test(`${group ? "Group" : "Tool"} original-text reversion stays dirty after an acknowledged save and refresh (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"admin", is_admin:true}};});
    // A real prior mutation makes a full configuration read eligible while the
    // subsequent editor's save is pending.
    await panel.locator(".tool-enabled").first().uncheck();
    await expect.poll(() => panel.evaluate(host => host._configData.config.functions[0].enabled)).toBe(false);
    await panel.locator(group ? ".edit-group" : ".edit-tool").first().click();
    if (!group) await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    const field = panel.locator(group ? "#group-description" : "#tool-yaml");
    const original = await field.inputValue();
    const revision = await panel.evaluate((host, group) => group ? host._groupRevision : host._toolRevision, group);
    await panel.evaluate((host, group) => {
      const call = host.hass.callWS.bind(host.hass);
      window.repairOwnership = {entered:false, saves:[], baseline:host._configData.config};
      host.hass.callWS = async message => {
        if (message.section === "tools" && message.action === (group ? "save_group" : "save")) {
          window.repairOwnership.saves.push(structuredClone(message));
          window.repairOwnership.entered = true;
          await new Promise(resolve => {window.repairOwnership.release = resolve;});
        }
        return call(message);
      };
    }, group);
    await field.fill(group ? "Acknowledged Group A" : original.replace("Baseline browser fixture Function Tool", "Acknowledged Tool A"));
    await panel.locator(group ? "#group-save" : "#tool-save").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.entered)).toBe(true);
    await field.fill(original);
    await panel.evaluate(host => host._loadSection(true));
    expect(await panel.evaluate(host => host._configData.config !== window.repairOwnership.baseline)).toBe(true);
    await settleReleasedOperation(page);
    await expect(field).toHaveValue(original);
    expect(await panel.evaluate((host, group) => group ? host._groupRevision : host._toolRevision, group)).toBe(revision);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    const agent = await panel.evaluate(host => host._agentId);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect(panel.locator("#confirm-dialog")).toBeVisible();
    await panel.locator("#confirm-cancel").click();
    expect(await panel.evaluate(host => host._agentId)).toBe(agent);
    await expect(field).toHaveValue(original);
    await panel.evaluate(host => {void host._navigate("guide");});
    await expect(panel.locator("#confirm-dialog")).toBeVisible();
    await panel.locator("#confirm-cancel").click();
    await expect(field).toHaveValue(original);
    await panel.evaluate(host => {
      const parent = host.parentElement, hass = host.hass;
      host.remove();
      const next = document.createElement("extended-openai-management-panel");
      parent.append(next);
      next.hass = hass;
    });
    await expect.poll(() => panel.evaluate((host, group) => {
      const id = group ? "group-description" : "tool-yaml";
      const field = host.shadowRoot.querySelector(`#${id}`);
      if (field?.closest("dialog")?.open) return field.value;
      const review = host.shadowRoot.querySelector('[aria-label="Recovered editor values"]');
      return review ? JSON.parse(review.value).find(value => value.id === id)?.value : null;
    }, group)).toBe(original);
    expect(await page.evaluate(() => window.repairOwnership.saves.length)).toBe(1);
    await expectHarnessClean(page, diagnostics);
  });

  for (const reopen of [false, true]) test(`Group acknowledgement preserves ${reopen ? "a reopened" : "the same"} editor's newer draft (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator(".edit-group").first().click();
    const initialRevision = await panel.evaluate(host => host._groupRevision);
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      const state = {entered:false, saves:[]};
      window.repairOwnership = state;
      host.hass.callWS = async message => {
        if (message.section === "tools" && message.action === "save_group") {
          state.saves.push(structuredClone(message));
          if (state.saves.length === 1) {
            state.entered = true;
            await new Promise(resolve => {state.release = resolve;});
          }
        }
        return original(message);
      };
    });
    await panel.locator("#group-description").fill("Submitted Group A");
    await panel.locator("#group-save").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.entered)).toBe(true);
    if (reopen) {
      await panel.locator("#group-cancel").click();
      await panel.locator(".edit-group").first().click();
    }
    await panel.locator("#group-description").fill("Newer unsaved Group B");
    await settleReleasedOperation(page);
    await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#group-description")).toHaveValue("Newer unsaved Group B");
    await expect(panel.locator("#group-save")).toBeEnabled();
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    expect(await panel.evaluate(host => host._configData.config.function_groups[0].description)).toBe("Submitted Group A");
    const revision = await panel.evaluate(host => host._configData.revision);
    if (reopen) expect(await panel.evaluate(host => host._groupRevision)).toBe(initialRevision);
    else {
      expect(await panel.evaluate(host => host._groupRevision)).toBe(revision);
      await panel.locator("#group-save").click();
      await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", false);
      const groups = await panel.evaluate(host => host._configData.config.function_groups);
      expect(groups).toHaveLength(1);
      expect(groups[0].description).toBe("Newer unsaved Group B");
      expect(await page.evaluate(() => window.repairOwnership.saves.at(-1).revision)).toBe(revision);
      expect(await page.evaluate(() => window.repairOwnership.saves.at(-1).original_id)).toBe("baseline-group");
    }
    await expectHarnessClean(page, diagnostics);
  });

  for (const repair of [false, true]) {
    const kind = repair ? "repair" : "healthy";
    test(`acknowledged ${kind} tool Save preserves and then saves newer YAML (${version})`, async ({page}) => {
      const diagnostics = trackPageErrors(page);
      const panel = repair ? await prepareRepair(page, bundled, "save") : await prepareHealthyOperation(page, bundled, "save");
      if (repair) await openRepair(panel, 0);
      const submitted = toolDraft(repair, "Submitted version A");
      const newer = toolDraft(repair, "Newer unsaved version B");
      await panel.locator("#tool-yaml").fill(submitted);
      await panel.locator("#tool-save").click();
      await expect.poll(() => page.evaluate(repair => repair ? window.repairOwnership.saveEntered : window.repairOwnership.entered, repair)).toBe(true);
      await panel.locator("#tool-yaml").fill(newer);
      await settleReleasedOperation(page);
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
      await expect(panel.locator("#tool-yaml")).toHaveValue(newer);
      await expect(panel.locator("#tool-save")).toBeEnabled();
      expect(await panel.evaluate(host => host._toolInitialYaml)).toBe(submitted);
      expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
      const acknowledgedRevision = await panel.evaluate(host => host._configData.revision);
      expect(await panel.evaluate(host => host._toolRevision)).toBe(acknowledgedRevision);
      expect(await panel.evaluate(host => host._repairToolIndex)).toBeNull();
      await panel.locator("#tool-save").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      const name = repair ? "repair_a" : "baseline_tool";
      const tools = await panel.evaluate((host, name) => host._configData.config.functions.filter(tool => tool.spec.name === name), name);
      expect(tools).toHaveLength(1);
      expect(tools[0].spec.description).toBe("Newer unsaved version B");
      const lastSave = await page.evaluate(repair => repair ? window.repairOwnership.normalSaves.at(-1) : window.repairOwnership.saves.at(-1), repair);
      expect(lastSave.original_name).toBe(name);
      expect(lastSave.revision).toBe(acknowledgedRevision);
      await expectHarnessClean(page, diagnostics);
    });

    test(`late ${kind} validation cannot certify newly edited YAML (${version})`, async ({page}) => {
      const diagnostics = trackPageErrors(page);
      const panel = repair ? await prepareRepair(page, bundled, "validation") : await prepareHealthyOperation(page, bundled, "validate_yaml");
      if (repair) await openRepair(panel, 0);
      await panel.locator("#tool-yaml").fill(toolDraft(repair, "Submitted validation A"));
      await panel.locator("#tool-validate").click();
      await expect.poll(() => page.evaluate(repair => repair ? window.repairOwnership.validationEntered : window.repairOwnership.entered, repair)).toBe(true);
      const newer = toolDraft(repair, "Unvalidated version B");
      await panel.locator("#tool-yaml").fill(newer);
      await settleReleasedOperation(page);
      await expect(panel.locator("#tool-yaml")).toHaveValue(newer);
      await expect(panel.locator("#tool-error")).toHaveText("YAML changed; validate to refresh metadata.");
      expect(await page.evaluate(() => window.repairOwnership.saves)).toEqual([]);
      await expectHarnessClean(page, diagnostics);
    });
  }

  test(`late repair Validate cannot replace a different repair's diagnostics (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled, "validation");
    await openRepair(panel, 0);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_a"));
    await panel.locator("#tool-validate").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.validationEntered)).toBe(true);
    await panel.locator("#tool-cancel").click();
    await openRepair(panel, 1);
    const before = await panel.locator("#tool-error").textContent();
    await settleReleasedOperation(page);
    await expect(panel.locator("#tool-error")).toHaveText(before);
    expect(await panel.evaluate(host => host._repairToolIndex)).toBe(1);
    await expectHarnessClean(page, diagnostics);
  });

  test(`cancelled Function repair validation cannot save or close a later draft (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled, "validation");
    await openRepair(panel, 0);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_a"));
    await panel.locator("#tool-save").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.validationEntered)).toBe(true);
    await panel.locator("#tool-cancel").click();
    await openRepair(panel, 1);
    const nextDraft = `${repairedYaml("repair_b")}\n\n# New unsaved repair B`;
    await panel.locator("#tool-yaml").fill(nextDraft);
    await settleReleasedOperation(page);
    expect(await page.evaluate(() => window.repairOwnership.saves)).toEqual([]);
    expect(await page.evaluate(() => window.repairOwnership.raw[0].spec.parameters.properties.message.minLength)).toBe("1");
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(nextDraft);
    expect(await panel.evaluate(host => host._repairToolIndex)).toBe(1);
    await expect(panel.locator("#tool-save")).toBeEnabled();
    await expectHarnessClean(page, diagnostics);
  });

  test(`acknowledged Function repair preserves a newer open repair draft (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled, "save");
    await openRepair(panel, 0);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_a"));
    await panel.locator("#tool-save").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.saveEntered)).toBe(true);
    await panel.locator("#tool-cancel").click();
    await openRepair(panel, 1);
    const nextDraft = `${repairedYaml("repair_b")}\n\n# New unsaved repair B`;
    await panel.locator("#tool-yaml").fill(nextDraft);
    await settleReleasedOperation(page);
    await expect.poll(() => panel.evaluate(host => host._configData.revision)).toBe("repair-saved-1");
    expect(await panel.evaluate(host => host._configData.config.functions.map(tool => tool.spec.name))).toEqual(["repair_a", "baseline_tool"]);
    expect(await panel.evaluate(host => host._configData.function_repair.invalid_tools.map(tool => tool.name))).toEqual(["repair_b"]);
    expect(await page.evaluate(() => window.repairOwnership.saves.map(message => message.index))).toEqual([0]);
    expect(await page.evaluate(() => window.repairOwnership.raw[1].spec.parameters.properties.message.minLength)).toBe("1");
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(nextDraft);
    expect(await panel.evaluate(host => host._repairToolIndex)).toBe(1);
    expect(await panel.evaluate(host => host._repairToolRevision)).not.toBe("repair-saved-1");
    await expect(panel.locator("#tool-save")).toBeEnabled();
    await expectHarnessClean(page, diagnostics);
  });

  test(`Function repair acknowledges and refreshes a successful current editor (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled);
    await openRepair(panel, 0);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_a"));
    await panel.locator("#tool-save").click();
    await expect.poll(() => panel.evaluate(host => host._configData.revision)).toBe("repair-saved-1");
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await expect(panel.locator(".edit-invalid-tool")).toHaveCount(1);
    expect(await panel.evaluate(host => host._configData.config.functions.map(tool => tool.spec.name))).toEqual(["repair_a", "baseline_tool"]);
    expect(await page.evaluate(() => window.repairOwnership.saves.map(message => message.index))).toEqual([0]);
    expect(await page.evaluate(() => window.repairOwnership.raw[1].spec.parameters.properties.message.minLength)).toBe("1");
    await openRepair(panel, 1);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_b"));
    await panel.locator("#tool-save").click();
    await expect(panel.locator(".edit-invalid-tool")).toHaveCount(0);
    expect(await panel.evaluate(host => host._selectedAgent().configuration_issue)).toBeUndefined();
    expect(await panel.evaluate(host => host._configData.function_repair.invalid_count)).toBe(0);
    const valid = await panel.evaluate(host => host._call("configuration", "validate", {config:host._draft}));
    expect(valid.valid).toBe(true);
    await expectHarnessClean(page, diagnostics);
  });

  test(`queued close from a previous repair cannot clear a newly opened target (${version})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled);
    await openRepair(panel, 0);
    await panel.evaluate(host => {
      // HA dialog.close dispatches its close event in a later task. Reopen B
      // immediately so the old event must not reset B's ownership.
      host.shadowRoot.querySelector("#tool-cancel").click();
      host.shadowRoot.querySelector('.edit-invalid-tool[data-repair-index="1"]').click();
    });
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    expect(await panel.evaluate(host => host._repairToolIndex)).toBe(1);
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_b"));
    await panel.locator("#tool-save").click();
    await expect.poll(() => page.evaluate(() => window.repairOwnership.saves.map(message => message.index))).toEqual([1]);
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await expectHarnessClean(page, diagnostics);
  });
}

for (const bundled of [false, true]) for (const invalid of [false, true]) for (const failCatalogue of [false, true]) for (const replace of [false, true]) {
  test(`Function deletion preserves a newer ${invalid ? "quarantined" : "healthy"} sibling editor, catalogue failure=${failCatalogue}, replacement=${replace} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    const panel = await prepareRepair(page, bundled);
    await panel.evaluate((host, failCatalogue) => {
      host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
      const original = host.hass.callWS.bind(host.hass), state = window.repairOwnership;
      Object.assign(state, {deleteEntered:false, deletes:[], catalogueReads:0, catalogueAvailable:!failCatalogue});
      host.hass.callWS = async message => {
        if (message.section === "function_repair" && message.action === "delete_one") {
          state.deletes.push(structuredClone(message));
          if (message.revision !== state.revision) throw new Error("Saved repair changed; reload before deleting");
          state.deleteEntered = true;
          await new Promise(resolve => {state.releaseDelete = resolve;});
          state.raw.splice(Number(message.index), 1);
          state.revision = "deleted-a";
          const saved = state.snapshot();
          return {valid:true, ...saved, agent:{...host._selectedAgent(), function_count:saved.config.functions.length}};
        }
        if (!message.section && message.action === "agents") {
          state.catalogueReads++;
          if (!state.catalogueAvailable) throw new Error("Temporary catalogue read failure");
        }
        return original(message);
      };
    }, failCatalogue);
    await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
    await panel.locator('#confirm-accept').click();
    await expect.poll(() => page.evaluate(() => repairOwnership.deleteEntered)).toBe(true);
    if (invalid) await openRepair(panel, 1);
    else {
      await panel.getByText("Show member functions", {exact:true}).click();
      await panel.locator('.edit-tool').first().click();
      await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    }
    const authoredRevision = await panel.evaluate((host, invalid) => invalid ? host._repairToolRevision : host._toolRevision, invalid);
    const yaml = repairedYaml(invalid ? "repair_b" : "baseline_tool").replace("Corrected baseline_tool", "Newer healthy sibling draft");
    await panel.locator('#tool-yaml').fill(yaml);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    await page.evaluate(() => repairOwnership.releaseDelete());
    await expect.poll(() => panel.evaluate(host => host._eocAgentMutations || 0)).toBe(0);
    await expect(panel.locator('#toast')).toContainText("Function Tool deleted");
    await expect(panel.locator('#tool-dialog')).toBeVisible();
    await expect(panel.locator('#tool-yaml')).toHaveValue(yaml);
    expect(await panel.evaluate((host, invalid) => invalid ? host._repairToolRevision : host._toolRevision, invalid)).toBe(authoredRevision);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    expect(await page.evaluate(() => repairOwnership.catalogueReads)).toBe(0);
    expect(await page.evaluate(() => repairOwnership.deletes.length)).toBe(1);
    expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
    expect(await page.evaluate(() => repairOwnership.raw[0].spec.parameters.properties.message.minLength)).toBe("1");

    // Deletion shifts B's persisted index. Keeping its old authoring revision
    // must reject a stale save instead of writing into the healthy sibling now
    // occupying that index. Healthy editors retain the same revision guard.
    await panel.locator('#tool-save').click();
    await expect(panel.locator(invalid ? '#toast' : '#tool-error')).toContainText("Saved repair changed; reload before saving");
    await expect(panel.locator('#tool-save')).toBeEnabled();
    await expect(panel.locator('#tool-yaml')).toHaveValue(yaml);
    expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
    expect(await page.evaluate(() => repairOwnership.raw[0].spec.parameters.properties.message.minLength)).toBe("1");
    const attempted = await page.evaluate(invalid => invalid ? repairOwnership.saves.at(-1) : repairOwnership.normalSaves.at(-1), invalid);
    expect(attempted.revision).toBe(authoredRevision);
    if (invalid) expect(attempted.index).toBe(1);

    if (replace) {
      // The new panel must preserve the stale draft for review without rebasing
      // it onto a different raw index or submitting the failed edit again.
      await panel.evaluate(host => {
        const parent = host.parentElement, hass = host.hass;
        host.remove();
        const next = document.createElement("extended-openai-management-panel");
        parent.append(next);
        next.hass = hass;
      });
      if (failCatalogue) {
        await expect.poll(() => panel.evaluate(host => host._error)).toBe("Temporary catalogue read failure");
        await expect(panel.locator('#tool-dialog')).not.toBeVisible();
        // Recovery still waits for the same owner's successful catalogue read;
        // the retained source holds the draft across this fail-closed boundary.
        await panel.evaluate(host => {
          repairOwnership.catalogueAvailable = true;
          return host._loadAgents();
        });
      }
      const recovered = panel.getByLabel("Recovered editor values");
      await expect(recovered).toBeVisible();
      expect(JSON.parse(await recovered.inputValue()).find(value => value.id === "tool-yaml")?.value).toBe(yaml);
      expect(await page.evaluate(() => repairOwnership.deletes.length)).toBe(1);
    }
    expect(await page.evaluate(() => ({deletes:repairOwnership.deletes.length, repairs:repairOwnership.saves.length, normal:repairOwnership.normalSaves.length})))
      .toEqual({deletes:1, repairs:invalid ? 1 : 0, normal:invalid ? 0 : 1});
    expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
    expect(await page.evaluate(() => repairOwnership.raw[0].spec.parameters.properties.message.minLength)).toBe("1");
    await expectHarnessClean(page, diagnostics);
  });
}

for (const bundled of [false, true]) test(`quarantine deletion keeps the confirmed snapshot's revision (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  const panel = await prepareRepair(page, bundled);
  const authored = await panel.evaluate(host => host._result.revision);
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass), state = repairOwnership;
    state.deletes = [];
    host.hass.callWS = async message => {
      if (message.section === "function_repair" && message.action === "delete_one") {
        state.deletes.push(structuredClone(message));
        if (message.revision !== state.revision) throw new Error("Saved repair changed; reload before deleting");
        state.raw.splice(Number(message.index), 1);
        state.revision = "confirmed-deletion";
        const saved = state.snapshot();
        return {valid:true, ...saved, agent:{...host._selectedAgent()}};
      }
      return original(message);
    };
  });
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  // Another client removes A. The accepted read may advance the baseline, but
  // the open confirmation still describes A in the original raw collection.
  await panel.evaluate(async host => {
    repairOwnership.raw.shift();
    repairOwnership.revision = "remote-deleted-a";
    host._invalidateCleanConfiguration(host._agentId);
    await host._loadSection(true);
  });
  expect(await panel.evaluate(host => host._result.revision)).toBe("remote-deleted-a");
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#toast")).toContainText("Saved repair changed; reload before deleting");
  expect(await page.evaluate(() => repairOwnership.deletes[0].revision)).toBe(authored);
  expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
  await expect(panel.locator(".delete-invalid-tool")).toHaveCount(1);
  // A fresh action on the refreshed B card owns the new snapshot and can delete
  // B normally. The rejected confirmation must neither replay nor disable it.
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#toast")).toContainText("Function Tool deleted");
  expect(await page.evaluate(() => repairOwnership.deletes.map(message => message.revision))).toEqual([authored, "remote-deleted-a"]);
  expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["baseline_tool"]);
  await expectHarnessClean(page, diagnostics);
});

for (const bundled of [false, true]) test(`quarantine editor keeps its bound metadata revision after a deferred refresh (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  const panel = await prepareRepair(page, bundled);
  const authored = await panel.evaluate(host => host._result.revision);
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await panel.evaluate(async host => {
    window.retiredRepairEdit = host.shadowRoot.querySelector('.edit-invalid-tool[data-repair-index="1"]');
    repairOwnership.raw.shift();
    repairOwnership.revision = "remote-deleted-a";
    host._invalidateCleanConfiguration(host._agentId);
    await host._loadSection(true);
  });
  // Exercise the old card's still-bound handler before the deferred render.
  // Its displayed definition must not acquire the newer raw index's revision.
  await panel.evaluate(host => {
    host.shadowRoot.querySelector("#confirm-cancel").click();
    retiredRepairEdit.click();
  });
  await expect(panel.locator("#tool-dialog")).toBeVisible();
  expect(await panel.evaluate(host => host._repairToolRevision)).toBe(authored);
  await panel.locator("#tool-yaml").fill(repairedYaml("repair_b"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator("#toast")).toContainText("Saved repair changed; reload before saving");
  expect(await page.evaluate(() => repairOwnership.saves.at(-1).revision)).toBe(authored);
  expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
  expect(await page.evaluate(() => repairOwnership.raw[0].spec.parameters.properties.message.minLength)).toBe("1");
  await expectHarnessClean(page, diagnostics);
});

for (const bundled of [false, true]) test(`retired quarantine confirmation cannot dispatch after panel replacement (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  const panel = await prepareRepair(page, bundled);
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    repairOwnership.deletes = [];
    host.hass.callWS = message => {
      if (message.section === "function_repair" && message.action === "delete_one") {
        repairOwnership.deletes.push(structuredClone(message));
        throw new Error("A retired confirmation cannot dispatch");
      }
      return original(message);
    };
  });
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.evaluate(host => {
    window.retiredRepairConfirmation = host.shadowRoot.querySelector("#confirm-accept");
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
  await expect(panel.locator(".edit-invalid-tool")).toHaveCount(2);
  await page.evaluate(async () => {
    retiredRepairConfirmation.click();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  expect(await page.evaluate(() => repairOwnership.deletes)).toEqual([]);
  expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_a", "repair_b", "baseline_tool"]);
  await expectHarnessClean(page, diagnostics);
});

for (const bundled of [false, true]) for (const action of ["edit", "delete"]) test(`published unchanged repair cards adopt an unrelated saved revision for ${action} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  const panel = await prepareRepair(page, bundled);
  await panel.evaluate((host, action) => {
    window.preservedRepairButton = host.shadowRoot.querySelector(`.${action}-invalid-tool[data-repair-index="1"]`);
    const original = host.hass.callWS.bind(host.hass);
    repairOwnership.revision = "unrelated-title-save";
    repairOwnership.deletes = [];
    host.hass.callWS = async message => {
      if (message.section === "function_repair" && message.action === "delete_one") {
        repairOwnership.deletes.push(structuredClone(message));
        if (message.revision !== repairOwnership.revision) throw new Error("Saved repair changed; reload before deleting");
        repairOwnership.raw.splice(Number(message.index), 1);
        repairOwnership.revision = "deleted-after-title-save";
        return {valid:true, ...repairOwnership.snapshot(), agent:{...host._selectedAgent()}};
      }
      const response = await original(message);
      if ((message.section === "configuration" && message.action === "get")
          || (message.section === "function_repair" && message.action === "configuration_get")) return {...response, title:"Renamed by another administrator"};
      return response;
    };
    host._invalidateCleanConfiguration(host._agentId);
    return host._loadSection(true);
  }, action);
  expect(await panel.evaluate(host => host._configData.revision)).toBe("unrelated-title-save");
  expect(await panel.evaluate((host, action) => host.shadowRoot.querySelector(`.${action}-invalid-tool[data-repair-index="1"]`) === preservedRepairButton, action)).toBe(true);
  if (action === "edit") {
    await openRepair(panel, 1);
    expect(await panel.evaluate(host => host._repairToolRevision)).toBe("unrelated-title-save");
    await panel.locator("#tool-yaml").fill(repairedYaml("repair_b"));
    await panel.locator("#tool-save").click();
    await expect(panel.locator("#tool-dialog")).not.toBeVisible();
    expect(await page.evaluate(() => repairOwnership.saves)).toHaveLength(1);
    expect(await page.evaluate(() => repairOwnership.saves[0].revision)).toBe("unrelated-title-save");
    expect(await page.evaluate(() => repairOwnership.raw[1].spec.parameters.properties.message.minLength)).toBe(1);
  } else {
    await panel.locator('.delete-invalid-tool[data-repair-index="1"]').click();
    await panel.locator("#confirm-accept").click();
    await expect(panel.locator("#toast")).toContainText("Function Tool deleted");
    expect(await page.evaluate(() => repairOwnership.deletes)).toHaveLength(1);
    expect(await page.evaluate(() => repairOwnership.deletes[0].revision)).toBe("unrelated-title-save");
    expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_a", "baseline_tool"]);
  }
  await expectHarnessClean(page, diagnostics);
});

for (const bundled of [false, true]) test(`repair card bindings require their user until an authorized read is published (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  const panel = await prepareRepair(page, bundled);
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    repairOwnership.deletes = [];
    host.hass.callWS = async message => {
      if (message.section === "function_repair" && message.action === "delete_one") {
        repairOwnership.deletes.push(structuredClone(message));
        if (message.revision !== repairOwnership.revision) throw new Error("Saved repair changed; reload before deleting");
        repairOwnership.raw.splice(Number(message.index), 1);
        repairOwnership.revision = "deleted-by-current-user";
        return {valid:true, ...repairOwnership.snapshot(), agent:{...host._selectedAgent()}};
      }
      return original(message);
    };
    host.hass = {...host.hass, user:{id:"another-admin", is_admin:true}};
  });
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
  expect(await page.evaluate(() => repairOwnership.deletes)).toEqual([]);
  await panel.evaluate(host => {
    host._invalidateCleanConfiguration(host._agentId);
    return host._loadSection(true);
  });
  await panel.locator('.delete-invalid-tool[data-repair-index="0"]').click();
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#toast")).toContainText("Function Tool deleted");
  expect(await page.evaluate(() => repairOwnership.deletes)).toHaveLength(1);
  expect(await page.evaluate(() => repairOwnership.raw.map(tool => tool.spec.name))).toEqual(["repair_b", "baseline_tool"]);
  await expectHarnessClean(page, diagnostics);
});
