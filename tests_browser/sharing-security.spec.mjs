import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

test("Shared provider restoration is opt-in, previewed and configuration-dependent", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    browserHarness.hass.callWS = async message => {
      const result = await original(message);
      return message.action === "import_inspect"
        ? {...result, summary:{...result.summary, private_backup:true, provider_connection_available:true}}
        : result;
    };
  });
  await panel.locator("#transfer-export-mode").selectOption("full");
  const download = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  await panel.locator("#backup-file-transfer").setInputFiles(await (await download).path());
  const provider = panel.locator("#restore-provider-connection");
  await expect(provider).toBeVisible();
  await expect(provider).not.toBeChecked();
  await provider.check();
  await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(call => call.action === "import_inspect").at(-1)?.data?.restore_provider_connection)).toBe(true);
  await panel.locator('.transfer-restore-section[value="configuration"]').uncheck();
  await expect(provider).not.toBeChecked();
  await expect(provider).toBeDisabled();
  await panel.locator('.transfer-restore-section[value="configuration"]').check();
  await provider.check();
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await expect(panel.locator("#confirm-message")).toContainText(/provider connection and credentials for all its agents/i);
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "import_restore").at(-1)?.data?.restore_provider_connection)).toBe(true);
  await expectHarnessClean(page, errors);
});
