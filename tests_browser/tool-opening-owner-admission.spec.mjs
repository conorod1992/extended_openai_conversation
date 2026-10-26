import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const repairedTool = {
  spec:{name:"opening_repair", description:"Original opening's raw repair draft", parameters:{type:"object", properties:{
    message:{type:"string", minLength:1},
  }}},
  function:{type:"template", value_template:"{{ message | default('Hello') }}"},
};
const healthyYaml = "spec:\n  name: baseline_tool\n  description: Original opening's raw Tool draft\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n\n# Preserve this exact original-owner draft\n";

async function prepare(page, bundled, repair, {known = true, holdCatalog = false} = {}) {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  if (known) await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"tool-opening-owner-A", is_admin:true}};});
  await expect.poll(() => panel.evaluate(host => !host._eocCatalogBootstrap?.pending)).toBe(true);
  await panel.evaluate((host, {repair, repairedTool, holdCatalog}) => {
    const original = host.hass.callWS.bind(host.hass), baseline = structuredClone(host._configData);
    const broken = structuredClone(repairedTool);
    broken.spec.parameters.properties.message.minLength = "1";
    const error = "minLength must be a non-negative integer";
    const issue = {field:"functions", repairable:true, message:error};
    const state = window.toolOpeningOwner = {calls:[], saved:false, revision:baseline.revision, persisted:broken};
    const snapshot = () => ({...structuredClone(baseline), revision:state.revision,
      config:{...structuredClone(baseline.config), functions:state.saved
        ? [structuredClone(state.persisted), ...structuredClone(baseline.config.functions)]
        : structuredClone(baseline.config.functions)},
      function_repair:state.saved ? null : {
        invalid_tools:[{index:0, name:broken.spec.name, tool:structuredClone(broken), yaml:JSON.stringify(broken,null,2), validation_error:error}],
        invalid_count:1, group_issues:[], persisted_groups:structuredClone(baseline.config.function_groups),
        tools_valid:false, isolatable:true, validation_error:error,
      },
    });
    host.hass.callWS = async request => {
      state.calls.push(structuredClone(request));
      if (holdCatalog && request.section === "tools" && request.action === "built_in_catalog" && !state.catalogHeld) {
        state.catalogHeld = true;
        await new Promise(resolve => {state.releaseCatalog = resolve;});
        return {functions:[{implementation:"held_unknown_preset", label:"Retired unknown owner preset", already_configured:false,
          yaml:"spec:\n  name: held_unknown_preset\nfunction:\n  type: script\n  sequence: []\n"}]};
      }
      if (repair && request.section === "tools" && request.action === "validate_yaml") {
        const config = JSON.parse(request.yaml);
        return {valid:true, config, name:config.spec.name, type:config.function.type};
      }
      if (repair && request.section === "function_repair" && request.action === "save_one") {
        if (request.revision !== state.revision || request.index !== 0) throw Error("Stale Tool repair target");
        state.persisted = structuredClone(request.tool);
        state.saved = true;
        state.revision += "-repair";
        const agent = {...host._selectedAgent()};
        delete agent.configuration_issue;
        return {valid:true, ...snapshot(), agent};
      }
      if (repair && ((request.section === "configuration" && request.action === "get")
          || (request.section === "function_repair" && request.action === "configuration_get"))) return snapshot();
      const response = await original(request);
      if (repair && !request.section && request.action === "agents") {
        for (const agent of response.agents) {
          if (agent.subentry_id !== host._agentId) continue;
          if (state.saved) delete agent.configuration_issue;
          else agent.configuration_issue = structuredClone(issue);
        }
      }
      return response;
    };
    if (repair) {
      host._selectedAgent().configuration_issue = issue;
      host._configData = snapshot();
      host._result = host._configData;
      host._draft = structuredClone(host._configData.config);
      host._render();
    }
  }, {repair, repairedTool, holdCatalog});
  return {panel, errors};
}

async function open(panel, repair, {waitForCatalog = true} = {}) {
  if (repair) await panel.locator(".edit-invalid-tool").first().click();
  else {
    if (!await panel.locator(".edit-tool").first().isVisible()) {
      await panel.getByText("Show member functions", {exact:true}).click();
    }
    await panel.locator(".edit-tool").first().click();
  }
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#tool-save")).toBeEnabled();
  await expect(panel.locator("#tool-validate")).toBeEnabled();
  if (repair && waitForCatalog) await expect(panel.locator("#built-in-function")).toBeEnabled();
  else await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
}

async function drain(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function presentation(panel) {
  return panel.evaluate(host => {
    const q = selector => host.shadowRoot.querySelector(selector);
    return {yaml:q("#tool-yaml").value, open:q("#tool-dialog").open,
      status:q("#tool-error").textContent, statusClass:q("#tool-error").className,
      metadata:q("#tool-dialog-meta").textContent, title:q("#tool-dialog-title").textContent,
      initialYaml:host._toolInitialYaml, revision:host._toolRevision, repairRevision:host._repairToolRevision,
      saveDisabled:q("#tool-save").disabled, validateDisabled:q("#tool-validate").disabled};
  });
}

for (const bundled of [false, true]) for (const repair of [false, true]) {
  for (const change of ["ABA", "one-way"]) for (const action of ["Validate", "Save"]) {
    test(`${repair ? "repair" : "normal"} Tool opening rejects fresh ${action} after ${change}, then admits a new opening (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const {panel, errors} = await prepare(page, bundled, repair);
      await open(panel, repair);
      const yaml = repair ? `${JSON.stringify(repairedTool,null,2)}\n\n` : healthyYaml;
      await panel.locator("#tool-yaml").fill(yaml);
      await drain(page);
      // There is no pending validation/save to retire. This NEW click must
      // remain bound to the owner that opened the existing editor.
      await panel.evaluate((host, change) => {
        host.hass = {...host.hass, user:{id:"tool-opening-owner-B", is_admin:true}};
        if (change === "ABA") host.hass = {...host.hass, user:{id:"tool-opening-owner-A", is_admin:true}};
      }, change);
      await drain(page);
      const before = await presentation(panel);
      const callIndex = await page.evaluate(() => toolOpeningOwner.calls.length);
      await panel.locator(action === "Validate" ? "#tool-validate" : "#tool-save").click();
      await drain(page);
      expect(await page.evaluate(index => toolOpeningOwner.calls.slice(index), callIndex)).toEqual([]);
      expect(await presentation(panel)).toEqual(before);
      await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);

      // Explicit Cancel discards this retired opening. A fresh UI opening
      // captures the current owner's authority and can use the same raw YAML.
      await panel.locator("#tool-cancel").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      await drain(page);
      await panel.evaluate(async host => {
        host._invalidateCleanConfiguration(host._agentId);
        await host._loadSection(true);
      });
      await open(panel, repair);
      await panel.locator("#tool-yaml").fill(yaml);
      const reopenedIndex = await page.evaluate(() => toolOpeningOwner.calls.length);
      await panel.locator(action === "Validate" ? "#tool-validate" : "#tool-save").click();
      if (action === "Validate") {
        await expect(panel.locator("#tool-error")).toContainText("Valid function tool");
        await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
        await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);
      } else {
        await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
        if (repair) expect(await page.evaluate(() => toolOpeningOwner.persisted)).toEqual(repairedTool);
        else expect(await page.evaluate(() => browserHarness.getState().configuration.config.functions.find(tool => tool.spec.name === "baseline_tool").spec.description))
          .toBe("Original opening's raw Tool draft");
      }
      const requests = await page.evaluate(index => toolOpeningOwner.calls.slice(index), reopenedIndex);
      expect(requests.filter(request => request.section === "tools" && request.action === "validate_yaml"))
        .toHaveLength(action === "Validate" || repair ? 1 : 0);
      expect(requests.filter(request => request.section === (repair ? "function_repair" : "tools")
        && request.action === (repair ? "save_one" : "save"))).toHaveLength(action === "Save" ? 1 : 0);
      await expectHarnessClean(page, errors);
    });
  }
}

for (const bundled of [false, true]) for (const repair of [false, true]) for (const action of ["Validate", "Save"]) {
  test(`${repair ? "repair" : "normal"} completed Tool opening admits first known identity for ${action} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, repair, {known:false});
    await open(panel, repair);
    const yaml = repair ? `${JSON.stringify(repairedTool,null,2)}\n\n` : healthyYaml;
    await panel.locator("#tool-yaml").fill(yaml);
    await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"tool-opening-first-known", is_admin:true}};});
    const index = await page.evaluate(() => toolOpeningOwner.calls.length);
    await panel.locator(action === "Validate" ? "#tool-validate" : "#tool-save").click();
    if (action === "Validate") {
      await expect(panel.locator("#tool-error")).toContainText("Valid function tool");
      await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);
      await expect(panel.locator("#tool-save")).toBeEnabled();
    } else {
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      if (repair) expect(await page.evaluate(() => toolOpeningOwner.persisted)).toEqual(repairedTool);
      else expect(await page.evaluate(() => browserHarness.getState().configuration.config.functions.find(tool => tool.spec.name === "baseline_tool").spec.description))
        .toBe("Original opening's raw Tool draft");
    }
    const requests = await page.evaluate(index => toolOpeningOwner.calls.slice(index), index);
    expect(requests.filter(request => request.section === "tools" && request.action === "validate_yaml"))
      .toHaveLength(action === "Validate" || repair ? 1 : 0);
    expect(requests.filter(request => request.section === (repair ? "function_repair" : "tools")
      && request.action === (repair ? "save_one" : "save"))).toHaveLength(action === "Save" ? 1 : 0);
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) {
  test(`unknown Tool catalogue operation stays retired after a fresh known-owner admission (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true, {known:false, holdCatalog:true});
    await open(panel, true, {waitForCatalog:false});
    await expect.poll(() => page.evaluate(() => typeof toolOpeningOwner.releaseCatalog)).toBe("function");
    const yaml = `${JSON.stringify(repairedTool,null,2)}\n\n`;
    await panel.locator("#tool-yaml").fill(yaml);
    await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"tool-opening-first-known", is_admin:true}};});
    await panel.locator("#tool-validate").click();
    await expect(panel.locator("#tool-error")).toContainText("Valid function tool");
    const before = await presentation(panel);
    await expect(panel.locator("#built-in-function")).toBeDisabled();
    await page.evaluate(() => toolOpeningOwner.releaseCatalog());
    await drain(page);
    expect(await presentation(panel)).toEqual(before);
    await expect(panel.locator("#built-in-function")).toBeDisabled();
    await expect(panel.locator('#built-in-function option[value="held_unknown_preset"]')).toHaveCount(0);
    await expect(panel.locator("#tool-yaml")).toHaveValue(yaml);
    await expectHarnessClean(page, errors);
  });
}
