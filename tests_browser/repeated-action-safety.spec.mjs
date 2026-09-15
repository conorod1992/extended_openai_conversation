import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Guest policy save ignores repeated activation while the mutation is pending", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode"));

  const panel = page.locator("extended-openai-management-panel");
  const save = panel.locator("#guest-policy-save");
  await expect(save).toBeVisible();

  await page.evaluate(() => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    window.browserHarness.guestPolicySaveCalls = 0;
    window.browserHarness.releaseGuestPolicySave = release;
    hass.callWS = async (request) => {
      if (request.section === "guest_mode" && request.action === "save_policy") {
        window.browserHarness.guestPolicySaveCalls += 1;
        await gate;
      }
      return original(request);
    };
  });

  await save.evaluate((button) => {
    const event = () => new MouseEvent("click", {bubbles: true, composed: true});
    button.dispatchEvent(event());
    button.dispatchEvent(event());
  });

  await expect.poll(() => page.evaluate(() => window.browserHarness.guestPolicySaveCalls)).toBe(1);
  await expect(save).toBeDisabled();
  await expect(save).toHaveText("Saving…");

  await page.evaluate(() => window.browserHarness.releaseGuestPolicySave());
  await expect(save).toBeEnabled();
  await expect(save).toHaveText("Save Guest policy");

  expect(await page.evaluate(() => window.browserHarness.guestPolicySaveCalls)).toBe(1);
  const persistedCalls = await page.evaluate(() => window.browserHarness.calls.filter(
    (request) => request.section === "guest_mode" && request.action === "save_policy",
  ).length);
  expect(persistedCalls).toBe(1);
  await expectHarnessClean(page, pageErrors);
});
