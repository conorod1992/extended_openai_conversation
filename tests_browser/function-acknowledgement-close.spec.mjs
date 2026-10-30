import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["tool", "group", "repair"]) {
  test(`${kind} acknowledges identity before failed modal close (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#function-add")).toBeVisible();
    if (kind === "repair") {
      await panel.evaluate(host => {
        const baseline = structuredClone(host._configData), original = host.hass.callWS.bind(host.hass);
        const issue = {field:"functions", repairable:true, message:"Saved tool needs repair"};
        const repair = {invalid_tools:[{index:0, name:"repaired_tool", tool:{}, yaml:"invalid legacy tool", validation_error:issue.message}],
          invalid_count:1, isolatable:true, tools_valid:false, group_issues:[], persisted_groups:baseline.config.function_groups, validation_error:issue.message};
        host.hass.callWS = async request => {
          if (request.section !== "function_repair" || request.action !== "save_one") return original(request);
          browserHarness.calls.push(structuredClone(request));
          const result = await original({...request, section:"tools", action:"save", tool:request.tool});
          return {...baseline, revision:result.revision, function_repair:null,
            config:{...baseline.config, functions:result.functions, function_groups:result.function_groups}};
        };
        host._configData = {...baseline, function_repair:repair};
        host._result = host._configData;
        host._selectedAgent().configuration_issue = issue;
        host._render();
      });
      await panel.locator(".edit-invalid-tool").click();
    } else {
      await panel.locator("#function-add").click();
      await panel.locator(kind === "group" ? "#add-group" : "#add-tool").click();
    }
    const editor = kind === "group" ? "group" : "tool";
    if (kind === "group") {
      await panel.locator("#group-name").fill("Committed group");
      await panel.locator("#group-id").fill("committed-group");
      await panel.locator("#group-description").fill("Committed A");
      await panel.locator('#group-functions input[value="baseline_tool"]').check();
    } else await panel.locator("#tool-yaml").fill(browserToolYaml("Committed A").replace("name: browser_tool", `name: ${kind === "repair" ? "repaired_tool" : "browser_tool"}`));
    await panel.evaluate((host, editor) => {
      const dialog = host.shadowRoot.querySelector(`#${editor}-dialog`), close = dialog.close;
      dialog.close = () => { throw Error("injected modal close failure"); };
      window.restoreFunctionClose = () => { dialog.close = close; };
    }, editor);
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator("#toast")).toContainText(/(?:saved|repaired), but .*could not be refreshed/);
    await expect(panel.locator(`#${editor}-save`)).toBeEnabled();
    expect(await panel.evaluate((host, kind) => kind === "group" ? host._groupOriginalId : host._toolOriginalName, kind)).toBe(kind === "group" ? "committed-group" : kind === "repair" ? "repaired_tool" : "browser_tool");
    await page.evaluate(() => restoreFunctionClose());
    if (kind === "group") await panel.locator("#group-description").fill("Newer B");
    else await panel.locator("#tool-yaml").fill(browserToolYaml("Newer B").replace("name: browser_tool", `name: ${kind === "repair" ? "repaired_tool" : "browser_tool"}`));
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open", false);
    const saved = await page.evaluate(kind => {
      const state = browserHarness.getState().configuration.config;
      return {records:kind === "group" ? state.function_groups : state.functions,
        writes:browserHarness.calls.filter(call => call.section === "tools" && call.action === (kind === "group" ? "save_group" : "save"))};
    }, kind);
    expect(saved.records).toHaveLength(2);
    expect(saved.writes).toHaveLength(2);
    expect(saved.writes.at(-1)[kind === "group" ? "original_id" : "original_name"]).toBe(kind === "group" ? "committed-group" : kind === "repair" ? "repaired_tool" : "browser_tool");
    await expectHarnessClean(page, diagnostics);
  });
}
