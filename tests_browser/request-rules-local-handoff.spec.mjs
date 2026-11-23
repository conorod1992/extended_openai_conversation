import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Legacy matching repair requires review and preserves Continue to AI", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  const card = panel.locator(".request-rule-card").first();
  await expect(card).toBeVisible();
  await panel.evaluate(host => {
    Object.assign(host._result.rules[0], {
      action_type: "local_action",
      action: {actions: [{action: "light.turn_on"}], continue_to_ai: false},
      continue_matching: true,
    });
  });
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-matching-repair")).toBeVisible();
  await panel.locator("#rule-local-continue-to-ai").check();
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Confirm the repaired rule");
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "update" && call.section === "request_rules"))).toEqual([]);

  await panel.locator("#rule-matching-repair-confirm").check();
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
  const rule = await page.evaluate(() => browserHarness.getState().requestRules.rules[0]);
  expect(rule).not.toHaveProperty("continue_matching");
  expect(rule.action.continue_to_ai).toBe(true);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-matching-repair")).toBeHidden();
  await panel.locator("#rule-local-continue-to-ai").uncheck();
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
  const updated = await page.evaluate(() => browserHarness.getState().requestRules.rules[0]);
  expect(updated.action.continue_to_ai).toBe(false);
  await expectHarnessClean(page, errors);
});

for (const bundle of [false, true]) {
  test(`safe match preview distinguishes terminal and conditional outcomes (${bundle ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#rule-match-test-text")).toBeVisible();
    await page.waitForFunction(() => browserHarness.panel._result && !browserHarness.panel._busy);
    // These wire shapes are verified separately against genuine HA Script.
    // Here the real panel must not turn a possible handoff into a promise.
    await page.evaluate(() => {
      const panel = browserHarness.panel;
      const original = panel._call.bind(panel);
      browserHarness.previewCalls = [];
      panel._call = async (section, action, data) => {
        if (section !== "request_rules" || action !== "test_match") return original(section, action, data);
        browserHarness.previewCalls.push({section, action, data});
        const first = {id:"first",name:"First",match_type:"equals",action_type:"local_action"};
        if (data.text === "terminal") {
          const would_do = {type:"local_action",action_count:1,terminal:true,consumed:true,provider_input:"none"};
          return {matched:true,rule:first,would_do,matched_rules:[{rule:first,status:"stopped",would_do,ai_input:null}]};
        }
        const would_do = {type:"local_action",action_count:1,conditional:true,consumed:null,provider_input:"conditional"};
        return {matched:true,conditional:true,rule:first,would_do,matched_rules:[
          {rule:first,status:"conditional",would_do,ai_input:{mode:"original",conditional:true,provider_input:"ask <later>"}},
        ]};
      };
    });
    const input = panel.locator("#rule-match-test-text");
    const output = panel.locator("#rule-match-test-result");
    await input.fill("terminal");
    await panel.locator("#rule-match-test").click();
    await expect(output.locator("strong")).toHaveText("Matched: First");
    await expect(output).toContainText("command would be consumed locally");
    await expect(output).not.toContainText("Second");
    await expect(output).not.toContainText("AI input");

    await input.fill("uncertain");
    await input.press("Enter");
    await expect(output.locator("strong")).toHaveText("Conditional match: First");
    await expect(output).toContainText("Possible AI input");
    await expect(output).toContainText("Original request → ask <later>");
    await expect(output).toContainText("Actions may stop or fail");
    expect(await page.evaluate(() => browserHarness.previewCalls.map(call => call.data.text))).toEqual(["terminal", "uncertain"]);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "test"))).toEqual([]);
    await expectHarnessClean(page, errors);
  });
}
