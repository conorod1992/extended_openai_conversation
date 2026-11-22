import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("repeated Request Rules binding keeps one wording-add and one create-rule activation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".wording-editor > summary").click();
  await expect(panel.locator("#wording-add")).toBeVisible();
  const before = await panel.locator(".wording-group").count();
  await panel.evaluate(async host => {
    const {bindRequestRulesCore} = await import("/custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js");
    window.ruleCreateActivations = 0;
    for (let pass = 0; pass < 4; pass++) bindRequestRulesCore(host, {openEditor:() => { window.ruleCreateActivations++; }});
  });
  await panel.locator("#wording-add").click();
  await expect(panel.locator(".wording-group")).toHaveCount(before + 1);
  await panel.locator("#rule-add").click();
  expect(await page.evaluate(() => window.ruleCreateActivations)).toBe(1);
  await expectHarnessClean(page, errors);
});
