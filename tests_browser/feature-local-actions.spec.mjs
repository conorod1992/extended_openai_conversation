import {test, expect} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) {
  test(`Temporary Memory keeps shared dirty guards and single-save behavior (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, "temporary", 20, bundled);
    await panel.evaluate(host => { host._render(); host._bindActions(); host._bindActions(); });
    const card = panel.locator('[data-memory-id="temporary-4"]');
    await card.locator(".card-main").focus();
    await card.locator(".card-main").press("Enter");
    const dialog = panel.locator("#temporary-memory-dialog");
    await expect(dialog).toHaveJSProperty("open", true);
    await panel.locator("#temporary-memory-content").fill("Unsaved short-term edit");
    await page.keyboard.press("Escape");
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#confirm-cancel").click();
    await expect(dialog).toHaveJSProperty("open", true);
    await expect(panel.locator("#temporary-memory-content")).toHaveValue("Unsaved short-term edit");
    await dialog.getByRole("button", {name: "Close", exact: true}).click();
    await acceptConfirmation(panel);
    await expect(dialog).toHaveJSProperty("open", false);
    await card.locator("button.edit-temporary-memory").click();
    await expect(panel.locator("#temporary-memory-content")).toHaveValue("Temporary 4");
    await panel.locator("#temporary-memory-content").fill("Saved once");
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS;
      browserHarness.hass.callWS = message => {
        if (message.action === "temporary_update") {
          browserHarness.hass.callWS = original;
          return Promise.reject(new Error("Temporary save unavailable"));
        }
        return original(message);
      };
    });
    await panel.locator("#temporary-memory-save").click();
    await expect(panel.locator("#temporary-memory-error")).toHaveText("Temporary save unavailable");
    await expect(dialog).toHaveJSProperty("open", true);
    await expect(panel.locator("#temporary-memory-save")).toBeEnabled();
    await expect(panel.locator("#temporary-memory-content")).toHaveValue("Saved once");
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS;
      window.saveAttempts = 0;
      browserHarness.hass.callWS = async message => {
        if (message.action === "temporary_update") {
          saveAttempts++;
          await new Promise(resolve => { window.releaseTemporarySave = resolve; });
        }
        return original(message);
      };
    });
    await panel.locator("#temporary-memory-save").click();
    await expect(panel.locator("#temporary-memory-save")).toBeDisabled();
    await panel.locator("#temporary-memory-form").evaluate(form => form.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true})));
    expect(await page.evaluate(() => saveAttempts)).toBe(1);
    await page.evaluate(() => releaseTemporarySave());
    await expect(dialog).toHaveJSProperty("open", false);
    await expect(card).toContainText("Saved once");
    expect(await page.evaluate(() => browserHarness.panel._temporaryMemoryDraft)).toBeNull();
    await card.locator("button.edit-temporary-memory").click();
    const readsBeforeDelete = await page.evaluate(() => dataCollectionBackend.calls.filter(c => c.action === "temporary_list").length);
    await panel.locator("#temporary-memory-delete").click();
    await acceptConfirmation(panel);
    await expect(dialog).toHaveJSProperty("open", false);
    await expect(card).toHaveCount(0);
    expect(await page.evaluate(() => dataCollectionBackend.calls.filter(c => c.action === "temporary_delete"))).toHaveLength(1);
    expect(await page.evaluate(() => dataCollectionBackend.calls.filter(c => c.action === "temporary_list").length)).toBe(readsBeforeDelete);
    await expectHarnessClean(page, errors);
  });
}
