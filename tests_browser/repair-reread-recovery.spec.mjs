import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const revertOriginal of [false, true]) for (const failRead of [false, true]) {
  test(`repair draft survives replacement during ${failRead ? "failed" : "successful"} acknowledgement reread, original=${revertOriginal} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#function-add")).toBeVisible();
    await panel.evaluate((host, failRead) => {
      host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
      const baseline = structuredClone(host._configData), original = host.hass.callWS.bind(host.hass);
      const broken = {spec:{name:"repair_last", description:"Original legacy definition",
        parameters:{type:"object", properties:{message:{type:"string", minLength:"1"}}}},
        function:{type:"template", value_template:"Hello"}};
      const issue = {field:"functions", repairable:true, message:"minLength must be a non-negative integer"};
      const repair = {invalid_tools:[{index:0, name:"repair_last", tool:broken,
        yaml:JSON.stringify(broken, null, 2), validation_error:issue.message}],
        invalid_count:1, isolatable:true, tools_valid:false, group_issues:[],
        persisted_groups:baseline.config.function_groups, validation_error:issue.message};
      let snapshot = {...baseline, function_repair:repair};
      const state = {entered:false, rereadEntered:false, rereadFinished:false, reads:0, writes:0};
      window.repairReread = state;
      host.hass.callWS = async message => {
        if (message.section === "tools" && message.action === "set_enabled") {
          const result = await original(message);
          snapshot = {...baseline, config:{...baseline.config, functions:result.functions}, revision:result.revision, function_repair:repair};
          return {...result, function_repair:repair};
        }
        if (message.section === "tools" && message.action === "validate_yaml") {
          const config = JSON.parse(message.yaml);
          return {valid:true, config, name:config.spec.name, type:config.function.type};
        }
        if (message.section === "function_repair" && message.action === "save_one") {
          if (message.revision !== snapshot.revision) throw new Error("Repair revision changed");
          state.writes++;
          const agent = {...host._selectedAgent()};
          delete agent.configuration_issue;
          snapshot = {...baseline, revision:"repair-committed-1",
            config:{...snapshot.config, functions:[message.tool, ...snapshot.config.functions]}};
          const response = {...snapshot, agent};
          state.entered = true;
          await new Promise(resolve => {state.releaseAcknowledgement = resolve;});
          return response;
        }
        if (message.section === "configuration" && message.action === "get") {
          if (++state.reads === 2) {
            state.rereadEntered = true;
            try {
              await new Promise(resolve => {state.releaseRead = resolve;});
              if (failRead) throw new Error("Generated acknowledgement reread failure");
            } finally {state.rereadFinished = true;}
          }
          return structuredClone(snapshot);
        }
        if (message.section === "tools" && message.action === "serialize" && message.name === "repair_last") {
          if (message.revision !== snapshot.revision) throw new Error("Repaired Function revision changed");
          return {yaml:JSON.stringify(snapshot.config.functions[0], null, 2)};
        }
        return original(message);
      };
      host._configData = snapshot;
      host._result = snapshot;
      host._selectedAgent().configuration_issue = issue;
      host._render();
    }, failRead);
    await panel.getByText("Show member functions", {exact:true}).click();
    await panel.locator(".tool-enabled").first().uncheck();
    await expect.poll(() => panel.evaluate(host => host._configData.config.functions[0].enabled)).toBe(false);
    await panel.locator(".edit-invalid-tool").click();
    const original = await panel.locator("#tool-yaml").inputValue();
    const submitted = JSON.stringify({spec:{name:"repair_last", description:"Committed repair A",
      parameters:{type:"object", properties:{}}}, function:{type:"template", value_template:"Hello"}}, null, 2);
    await panel.locator("#tool-yaml").fill(submitted);
    await panel.locator("#tool-save").click();
    await expect.poll(() => page.evaluate(() => window.repairReread.entered)).toBe(true);
    const draft = revertOriginal ? original : submitted.replace("Committed repair A", "Newer unsaved repair B");
    await panel.locator("#tool-yaml").fill(draft);
    await panel.evaluate(host => host._loadSection(true));
    await page.evaluate(() => window.repairReread.releaseAcknowledgement());
    await expect.poll(() => page.evaluate(() => window.repairReread.rereadEntered)).toBe(true);
    // Dirty ownership must be published before awaiting this second read.
    expect(await panel.evaluate(host => host._toolInitialYaml)).toBe(submitted);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    await page.evaluate(() => {
      const source = document.querySelector("extended-openai-management-panel"), hass = source.hass;
      window.repairReread.source = source;
      source.remove(); // Runs HA panel teardown and real draft retention.
      const replacement = document.createElement("extended-openai-management-panel");
      document.body.append(replacement);
      replacement.hass = hass;
    });
    await expect(panel.locator("#function-add")).toBeVisible();
    const recovered = panel.getByLabel("Recovered editor values");
    await expect(recovered).toBeVisible();
    const recoveredYaml = async () => {
      const values = JSON.parse(await recovered.inputValue());
      return values.find(value => value.id === "tool-yaml")?.value;
    };
    expect(await recoveredYaml()).toBe(draft);
    await page.evaluate(() => window.repairReread.releaseRead());
    await expect.poll(() => page.evaluate(() => window.repairReread.rereadFinished)).toBe(true);
    await panel.evaluate(host => host._loadSection(true));
    expect(await recoveredYaml()).toBe(draft);
    expect(await page.evaluate(() => window.repairReread.writes)).toBe(1);
    await panel.getByRole("button", {name:"Discard recovered edits", exact:true}).click();
    await expect(recovered).toHaveCount(0);
    await panel.evaluate(host => host._navigate("guide"));
    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    await panel.locator('[data-tool-key="repair_last"] .edit-tool').click();
    await expect(panel.locator("#tool-save")).toBeEnabled();
    await expect(panel.locator("#tool-yaml")).toHaveValue(submitted);
    await panel.locator("#tool-validate").click();
    await expect(panel.locator("#tool-error")).toContainText("Valid function tool");
    await panel.locator("#tool-cancel").click();
    expect(await page.evaluate(() => window.repairReread.writes)).toBe(1);
    await expectHarnessClean(page, diagnostics);
  });
}
