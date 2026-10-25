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
      raw, revision:baseline.revision, validations:[], saves:[],
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
        if (hold === "validation") await new Promise(resolve => {state.release = resolve;});
        const config = JSON.parse(message.yaml);
        if (typeof config.spec.parameters.properties.message.minLength !== "number") {
          throw new Error("The test must submit a corrected Function schema");
        }
        state.validationReturned = true;
        return {valid:true, config};
      }
      if (message.section === "function_repair" && message.action === "save_one") {
        state.saves.push(structuredClone(message));
        state.saveEntered = true;
        if (hold === "save") await new Promise(resolve => {state.release = resolve;});
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

for (const bundled of [false, true]) {
  const version = bundled ? "bundle" : "source";

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
