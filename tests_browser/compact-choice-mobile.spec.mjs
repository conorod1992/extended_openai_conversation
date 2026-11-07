import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) {
  const asset = bundled ? "production bundle" : "source modules";

  test(`mobile Backup/Restore choices stay clickable with ${asset}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.setViewportSize({width: 375, height: 667});
    await page.goto(fixtureUrl("usage-maintenance/backup-restore", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator("#transfer-export-mode").selectOption("custom");
    const custom = panel.locator("#transfer-custom-options");
    await expect(custom).toBeVisible();
    const options = custom.locator(".group-function-choices");
    expect(await options.evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
    const knowledge = custom.locator('input.transfer-custom-section[value="knowledge"]');
    await expect(knowledge).toBeChecked();
    await knowledge.uncheck();
    await expect(knowledge).not.toBeChecked();
    await custom.getByText("Knowledge sources", {exact: true}).click();
    await expect(knowledge).toBeChecked();

    // The real import dialog is populated from a genuine fixture-generated backup.
    await panel.locator("#transfer-export-mode").selectOption("full");
    const download = page.waitForEvent("download");
    await panel.locator("#create-backup-transfer").click();
    const path = await (await download).path();
    await panel.locator("#backup-file-transfer").setInputFiles(path);
    const restore = panel.locator("#restore-dialog");
    await expect(restore).toHaveJSProperty("open", true);
    const restoreOptions = restore.locator(".group-function-choices");
    expect(await restoreOptions.evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
    const restoreKnowledge = restore.locator('input.transfer-restore-section[value="knowledge"]');
    await expect(restoreKnowledge).toBeChecked();
    await restoreKnowledge.uncheck();
    await expect(restoreKnowledge).not.toBeChecked();
    await restore.getByText("Knowledge sources", {exact: true}).click();
    await expect(restoreKnowledge).toBeChecked();
    await expectHarnessClean(page, errors);
  });

  test(`mobile Function Group options retain compact scrolling styles with ${asset}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.setViewportSize({width: 375, height: 667});
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator('.function-group-card[data-group-id="baseline-group"] .edit-group').click();
    const dialog = panel.locator("#group-dialog");
    await expect(dialog).toHaveJSProperty("open", true);
    const choices = dialog.locator(".group-function-choices");
    const style = await choices.evaluate(element => {
      const computed = getComputedStyle(element);
      return {display: computed.display, overflowY: computed.overflowY};
    });
    expect(style).toEqual({display: "grid", overflowY: "auto"});
    await panel.locator("#group-name").fill("");
    await panel.locator("#group-save").click();
    await expect(dialog.locator("#group-error")).toContainText("Group name is required");
    expect(await choices.evaluate(element => getComputedStyle(element).overflowY)).toBe("auto");
    await expectHarnessClean(page, errors);
  });
}
