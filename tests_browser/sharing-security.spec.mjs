import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

test("Sharing review displays exact content as text and requires custom-content acknowledgement", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#transfer-reviewed-custom")).not.toBeChecked();
  await expect(panel.getByText("Custom content you choose to include may still contain secrets.", {exact:false})).toBeVisible();
  await page.evaluate(async () => {
    const {reviewSharingExport} = await import("/custom_components/extended_openai_conversation_responses/frontend/sharing-review.js");
    const host = document.querySelector("extended-openai-management-panel");
    window.securityReviewResult = "pending";
    void reviewSharingExport(host, {json:'{"literal":"<img src=x onerror=alert(1)>"}',security:{review_required:true,omitted:[{count:2}]}}).then(value => {window.securityReviewResult=value;});
  });
  await expect(panel.locator("[data-summary]")).toContainText("2 configuration");
  await expect(panel.locator("[data-artifact]")).toHaveValue('{"literal":"<img src=x onerror=alert(1)>"}');
  await expect(panel.locator("dialog[aria-label='Review sharing export'] img")).toHaveCount(0);
  await expect(panel.locator("[data-download]")).toBeDisabled();
  await panel.locator("[data-ack]").check();
  await panel.locator("[data-download]").click();
  await expect.poll(() => page.evaluate(() => window.securityReviewResult)).toBe(true);
  await expectHarnessClean(page, errors);
});

test("Shareable Setup cancellation never downloads its generated artifact", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  let downloads = 0;
  page.on("download", () => {downloads += 1;});
  await panel.locator("#create-backup-transfer").click();
  await expect(panel.locator("[data-artifact]")).toBeVisible();
  await panel.locator("[data-cancel]").click();
  await expect(panel.locator("#create-backup-transfer")).toBeEnabled();
  expect(downloads).toBe(0);
  await expectHarnessClean(page, errors);
});

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
  await expect(panel.getByText(/shared provider connection and credentials for ALL/)).toBeVisible();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "import_restore").at(-1)?.data?.restore_provider_connection)).toBe(true);
  await expectHarnessClean(page, errors);
});
