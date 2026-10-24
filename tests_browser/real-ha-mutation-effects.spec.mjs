import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const scenario = process.env.MUTATION_EFFECTS_SCENARIO;
test.skip(!backendUrl || !scenario, "requires the genuine HA mutation-effects fixture");
const fixture = `/tests_browser/real-ha-fixture.html?bundle=1&route=${encodeURIComponent(scenario === "usage" ? "usage-maintenance/usage" : "capabilities/request-rules")}&backend=${encodeURIComponent(backendUrl)}`;

test("mutation declarations preserve real revisions and cached projections", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = page.locator("extended-openai-management-panel");
  await page.goto(fixture);
  await expect.poll(() => panel.evaluate(host => Boolean(host._result && !host._busy))).toBe(true);
  await page.evaluate(() => {
    const {hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.mutationEffectsProbe = {calls:[]};
    hass.callWS = async message => {
      const result = await original(message);
      window.mutationEffectsProbe.calls.push({message:structuredClone(message), result:structuredClone(result)});
      return result;
    };
  });
  if (scenario === "configuration_rules") {
    await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
    await panel.locator('[data-config="prompt"]').fill("Mutation effects saved prompt");
    await panel.locator("#save-config").click();
    await expect(panel.locator("#toast")).toContainText("Changes saved");
    expect(await panel.evaluate(host => host._sectionCache.has(`${host._agentId}|capabilities/request-rules`))).toBe(false);
    await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
    await expect.poll(() => page.evaluate(() => window.mutationEffectsProbe.calls.filter(c => c.message.section === "request_rules" && c.message.action === "list").length)).toBeGreaterThan(0);
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill("After configuration save");
    await panel.locator("#rule-phrases").fill("mutation test phrase");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("gpt-5-mini");
    await panel.locator("#rule-scope").selectOption("conversation");
    await panel.locator("#rule-save").click();
    await expect(panel.getByRole("heading", {name:"After configuration save", exact:true})).toBeVisible();
    const observed = await page.evaluate(() => window.mutationEffectsProbe.calls);
    const list = observed.find(c => c.message.section === "request_rules" && c.message.action === "list");
    const create = observed.find(c => c.message.section === "request_rules" && c.message.action === "create");
    expect(create.message.revision).toBe(list.result.revision);
    const fresh = await panel.evaluate(async host => ({config:await host._call("configuration", "get"), rules:await host._call("request_rules", "list")}));
    expect(fresh.config.config.prompt).toBe("Mutation effects saved prompt");
    expect(fresh.rules.rules.map(r => r.name)).toContain("After configuration save");
  } else if (scenario === "rule_pack") {
    const pack = await panel.evaluate(async host => {
      await host._call("request_rules", "create", {rule:{name:"Original pack rule", enabled:true, phrases:["pack phrase"], match_type:"equals", action_type:"model_routing", action:{model:"gpt-5-mini", scope:"conversation"}}});
      return host._call("request_rules", "rule_pack_export", {selection:"all", reviewed_custom:true});
    });
    pack.rules[0].name = "Imported pack rule";
    await panel.evaluate(host => host._loadSection());
    await panel.locator("#rule-sharing > summary").click();
    await panel.locator("#rule-pack-file").setInputFiles({name:"rules.json", mimeType:"application/json", buffer:Buffer.from(JSON.stringify(pack))});
    await panel.locator("#rule-pack-review-button").click();
    await expect(panel.locator("#rule-pack-confirm")).toBeVisible();
    // Hold an old genuine read across import acknowledgement: it must not restore the old revision.
    await page.evaluate(() => {
      const {hass, panel} = window.browserHarness;
      const original = hass.callWS.bind(hass);
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      let hold = true;
      hass.callWS = async message => {
        const result = await original(message);
        if (hold && message.section === "request_rules" && message.action === "list") {
          hold = false; window.mutationEffectsProbe.oldReadReady = true; await gate;
        }
        return result;
      };
      window.mutationEffectsProbe.releaseOldRead = release;
      window.mutationEffectsProbe.oldRead = panel._call("request_rules", "list");
    });
    await expect.poll(() => page.evaluate(() => Boolean(window.mutationEffectsProbe.oldReadReady))).toBe(true);
    await panel.locator("#rule-pack-confirm").click();
    await expect(panel.locator(".request-rule-card").filter({hasText:"Imported pack rule"})).toBeVisible();
    const acknowledgement = await page.evaluate(() => window.mutationEffectsProbe.calls.find(c => c.message.action === "rule_pack_import"));
    expect(acknowledgement).toBeTruthy();
    await page.evaluate(async () => { window.mutationEffectsProbe.releaseOldRead(); await window.mutationEffectsProbe.oldRead; });
    expect(await panel.evaluate(host => host._eocRequestRuleRevision.value)).toBe(acknowledgement.result.revision);
    await panel.evaluate(host => host._call("request_rules", "groups", {groups:[]}));
    const following = await page.evaluate(() => window.mutationEffectsProbe.calls.find(c => c.message.section === "request_rules" && c.message.action === "groups"));
    expect(following.message.revision).toBe(acknowledgement.result.revision);
    await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
    await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
    await expect(panel.locator(".request-rule-card").filter({hasText:"Imported pack rule"})).toBeVisible();
  } else {
    await expect(panel.locator("#usage-window")).toBeVisible();
    await panel.locator("#usage-window").selectOption("7");
    await expect.poll(() => panel.evaluate(host => host._usageHistoryWindow)).toBe("7");
    await panel.locator("#usage-window").selectOption("90");
    await expect.poll(() => panel.evaluate(host => host._usageHistoryWindow)).toBe("90");
    const before = await panel.evaluate(async host => ({runs:await host._call("usage", "runs"), keys:[...host._eocUsageWindowCache.keys()]}));
    expect(before.runs.runs.length).toBeGreaterThan(0);
    expect(before.keys.some(k => k.includes("|7|"))).toBe(true);
    expect(before.keys.some(k => k.includes("|90|"))).toBe(true);
    await panel.evaluate(host => host._call("usage", "clear_details", {confirm:true}));
    expect(await panel.evaluate(host => [...host._eocUsageWindowCache.keys()].filter(k => k.startsWith(`${host._agentId}|`)))).toEqual([]);
    expect(await panel.evaluate(host => [...host._sectionCache.keys()].some(k => k.startsWith(`${host._agentId}|usage-maintenance/usage`)))).toBe(false);
    await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
    await panel.evaluate(host => host._navigate("usage-maintenance", "usage"));
    await expect.poll(() => panel.evaluate(host => host._result?.runs?.runs?.length)).toBe(0);
    const fresh = await panel.evaluate(async host => ({summary:await host._call("usage", "summary"), runs:await host._call("usage", "runs")}));
    expect(fresh.runs.runs).toEqual([]);
    expect(fresh.summary.lifetime.total_tokens).toBe(17);
  }
  await expectHarnessClean(page, errors);
});
