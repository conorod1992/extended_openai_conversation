import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundle of [false, true]) for (const fault of ["missing groups", "missing rules", "lost reply"]) {
  test(`Rule Pack ${fault} protects the next persisted Group replacement (${bundle ? "bundle" : "source"})`, async ({page}) => {
    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-edit")).toBeVisible();
    const pack = await panel.evaluate((host, fault) => {
      const send = host.hass.callWS.bind(host.hass);
      window.packMutations = [];
      host.hass.callWS = async message => {
        if (message.section === "request_rules" && ["rule_pack_import", "update", "groups"].includes(message.action)) packMutations.push(structuredClone(message));
        const result = await send(message);
        if (message.section !== "request_rules" || message.action !== "rule_pack_import") return result;
        if (fault === "lost reply") throw new Error("Lost import acknowledgement");
        delete result[fault === "missing groups" ? "groups" : "rules"];
        return result;
      };
      return {format:"extended_openai_request_rule_pack", version:1,
        groups:[{id:"portable-group", name:"Imported group"}],
        rules:[{...structuredClone(host._result.rules[0]), id:"portable-rule", name:"Pack-owned rule",
          group_id:"portable-group", conditions:[], continue_matching:false, ai_input_mode:"original", ai_input_capture:null}]};
    }, fault);
    await panel.locator("#rule-sharing > summary").click();
    await panel.locator("#rule-pack-file").setInputFiles({name:"pack.json", mimeType:"application/json", buffer:Buffer.from(JSON.stringify(pack))});
    await panel.locator("#rule-pack-review-button").click();
    await panel.locator("#rule-pack-confirm").click();
    await expect(panel.locator("#rule-pack-message")).toContainText(/unknown/i);
    // This unrelated single-record operation must recover the unseen import
    // before a subsequent whole-Group replacement can use its revision.
    const baseline = panel.locator('[data-rule-key="rule-1"] .rule-enabled');
    await baseline.click();
    await expect(baseline).toBeEnabled();
    await panel.locator("#rule-groups-manage").click();
    await panel.locator("#rule-new-group-name").fill("Next group");
    await panel.locator("#rule-group-add").click();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.groups.some(group => group.name === "Next group"))).toBe(true);
    const saved = await page.evaluate(() => ({state:browserHarness.getState().requestRules, commands:packMutations}));
    expect(saved.state.groups.map(group => group.name)).toEqual(["Imported group", "Next group"]);
    const imported = saved.state.rules.find(rule => rule.name === "Pack-owned rule");
    expect(imported.group_id).toBe(saved.state.groups[0].id);
    expect(saved.commands.filter(command => command.action === "rule_pack_import")).toHaveLength(1);
    expect(saved.commands.filter(command => command.action === "update")).toHaveLength(0);
    await panel.locator("#rule-groups-done").click();
    await baseline.uncheck();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.id === "rule-1").enabled)).toBe(false);
    // The fixture rewrites history to the app route, which the static test
    // server cannot serve. Re-enter its page while retaining persisted storage.
    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    await expect(panel.locator(".request-rule-card").filter({hasText:"Pack-owned rule"}).locator(".rule-group-chip")).toHaveText("Imported group");
  });
}
