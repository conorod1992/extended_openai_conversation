import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

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
