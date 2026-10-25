import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) test(`a fresh healthy configuration read retires overtaken repair routing (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const diagnostics = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  await panel.evaluate(host => {
    const baseline = structuredClone(host._configData);
    const original = host.hass.callWS.bind(host.hass);
    const invalid = {spec:{name:"repair_last", description:"Legacy schema",
      parameters:{type:"object", properties:{message:{type:"string", minLength:"1"}}}},
      function:{type:"template", value_template:"Hello"}};
    const issue = {field:"functions", repairable:true, message:"minLength must be a non-negative integer"};
    const repair = {invalid_tools:[{index:0, name:"repair_last", tool:invalid,
      yaml:JSON.stringify(invalid), validation_error:issue.message}], invalid_count:1,
      isolatable:true, tools_valid:false, group_issues:[],
      persisted_groups:baseline.config.function_groups, validation_error:issue.message, tools:null};
    let snapshot = {...baseline, function_repair:repair};
    const state = {entered:false, reads:0, writes:0, calls:[]};
    window.repairRouting = state;
    host.hass.callWS = async message => {
      state.calls.push(structuredClone(message));
      if (message.section === "tools" && message.action === "set_enabled") {
        const result = await original(message);
        snapshot = {...baseline, config:{...baseline.config, functions:result.functions},
          revision:result.revision, function_repair:repair};
        return {...result, function_repair:repair};
      }
      if (message.section === "tools" && message.action === "validate_yaml") {
        return {valid:true, config:JSON.parse(message.yaml)};
      }
      if (message.section === "function_repair" && message.action === "save_one") {
        if (message.revision !== snapshot.revision) throw new Error("Repair revision changed");
        state.writes++;
        snapshot = {...baseline, revision:"repair-fixed-1",
          config:{...snapshot.config, functions:[message.tool, ...snapshot.config.functions]}};
        const agent = {...host._selectedAgent()};
        delete agent.configuration_issue;
        const acknowledgement = {...snapshot, agent};
        state.entered = true;
        await new Promise(resolve => {state.release = resolve;});
        return acknowledgement;
      }
      if (message.section === "configuration" && message.action === "get") {
        state.reads++;
        // The actual full GET certifies healthy Functions/Groups without an
        // agent snapshot or a function_repair field. Do not add either here.
        return structuredClone(snapshot);
      }
      if (message.section === "function_repair" && message.action === "configuration_validate") {
        throw new Error("Function Tools do not require repair");
      }
      return original(message);
    };
    host._configData = snapshot;
    host._result = snapshot;
    host._selectedAgent().configuration_issue = issue;
    host._render();
  });
  // Preserve a healthy sibling while naturally invalidating the full projection
  // through an ordinary public Function toggle.
  await panel.getByText("Show member functions", {exact:true}).click();
  await panel.locator(".tool-enabled").first().uncheck();
  await expect.poll(() => panel.evaluate(host => host._configData.config.functions[0].enabled)).toBe(false);
  await panel.locator(".edit-invalid-tool").click();
  await panel.locator("#tool-yaml").fill(JSON.stringify({
    spec:{name:"repair_last", description:"Corrected Function", parameters:{type:"object", properties:{}}},
    function:{type:"template", value_template:"Hello"},
  }));
  await panel.locator("#tool-save").click();
  await expect.poll(() => page.evaluate(() => window.repairRouting.entered)).toBe(true);
  await panel.evaluate(host => host._loadSection(true));
  expect(await panel.evaluate(host => host._selectedAgent().configuration_issue)).toBeUndefined();
  await page.evaluate(() => window.repairRouting.release());
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  await expect.poll(() => page.evaluate(() => window.repairRouting.reads)).toBe(2);
  expect(await panel.evaluate(host => host._configData.revision)).toBe("repair-fixed-1");
  expect(await panel.evaluate(host => host._configData.function_repair)).toBeUndefined();
  expect(await panel.evaluate(host => host._selectedAgent().configuration_issue)).toBeUndefined();
  const validation = await panel.evaluate(host => host._call("configuration", "validate", {config:host._draft}));
  expect(validation.valid).toBe(true);
  expect(await page.evaluate(() => window.repairRouting.calls.filter(call => call.action === "validate").at(-1)?.section)).toBe("configuration");
  expect(await page.evaluate(() => window.repairRouting.calls.filter(call => call.action === "configuration_validate"))).toEqual([]);
  expect(await page.evaluate(() => window.repairRouting.writes)).toBe(1);
  await expectHarnessClean(page, diagnostics);
});
