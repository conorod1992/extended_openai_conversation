import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("model validation preserves the next configuration field and its focus", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const model = panel.locator('[data-config="chat_model"]');
  const tokens = panel.locator('[data-config="max_tokens"]');
  await expect(model).toBeVisible();
  await panel.evaluate(host => {
    const original = host._hass.callWS.bind(host._hass);
    host._hass.callWS = async message => {
      const result = await original(message);
      if (message.section === "configuration" && message.action === "validate") {
        host._heldModelValidation = true;
        await new Promise(resolve => { host._releaseModelValidation = resolve; });
      }
      return result;
    };
  });
  await model.fill("gpt-5-mini nightly");
  await tokens.focus();
  await expect.poll(() => panel.evaluate(host => Boolean(host._heldModelValidation))).toBe(true);
  await tokens.fill("1234");
  await panel.evaluate(async host => {
    host._nextFieldBeforeValidation = host.shadowRoot.activeElement;
    host._releaseModelValidation();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  await expect(tokens).toBeFocused();
  await expect(tokens).toHaveValue("1234");
  await expect(model).toHaveValue("gpt-5-mini nightly");
  expect(await panel.evaluate(host => host.shadowRoot.activeElement === host._nextFieldBeforeValidation)).toBe(true);
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(model).toHaveValue("gpt-5-mini nightly");
  await expect(tokens).toHaveValue("1234");
  await expectHarnessClean(page, errors);
});
