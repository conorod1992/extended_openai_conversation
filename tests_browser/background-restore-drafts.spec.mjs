import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

for (const bundle of [false, true]) test.describe(bundle ? "bundle" : "source", () => {
for (const variant of ["unchanged", "newer edits", "baseline reversion", "failed refresh", "assistant", "authentication", "replacement", "replacement newer edits", "lost", "rejected"]) test(`background restore draft retirement: ${variant}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore", `&agents=2${bundle ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  if (variant.startsWith("replacement")) await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"restore-owner", is_admin:true}};});
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloading = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const path = await (await downloading).path();
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await panel.locator('[data-config="__title"]').fill("Persisted after backup");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Persisted after backup");
  await panel.locator('[data-config="__title"]').fill("Pre-restore stale title");
  await panel.locator('[data-config="max_tokens"]').fill("777");
  await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.evaluate((host, variant) => {
    window.restoreWrites = 0;
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      if (message.action === "import_restore" && variant === "rejected") {
        await new Promise(resolve => {window.releaseRestoreReply = resolve;});
        throw {code:"invalid_request", message:"Restore rejected"};
      }
      const result = await send(message);
      if (message.action === "import_restore") {
        restoreWrites++;
        await new Promise(resolve => {window.releaseRestoreReply = resolve;});
        if (variant === "lost") throw {type:"result", success:false, error:{code:3, message:"Reply lost"}};
      }
      if (window.failRestoreRead && message.section === "configuration" && message.action === "get") throw {code:"offline", message:"Read unavailable"};
      return result;
    };
  }, variant);
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => typeof releaseRestoreReply)).toBe("function");
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Pre-restore stale title");
  if (["newer edits", "failed refresh"].includes(variant)) {
    // Reverting to the original baseline is still a newer authored edit.
    await panel.locator('[data-config="__title"]').fill("Jarvis");
    await panel.locator('[data-config="max_tokens"]').fill("888");
  }
  if (variant === "baseline reversion") {
    await panel.locator('[data-config="__title"]').fill("Persisted after backup");
    await panel.locator('[data-config="max_tokens"]').fill(String(await panel.evaluate(host => host._configData.config.max_tokens)));
  }
  if (variant === "assistant") {
    await panel.evaluate(host => {host._confirm = async () => true;});
    await panel.locator("#agent").selectOption("scale-agent-1");
    await panel.locator('[data-config="__title"]').fill("Other assistant draft");
  }
  if (variant === "authentication") await panel.evaluate(host => {
    const hass = host.hass;
    host.hass = {...hass, user:{id:"intervening-user"}};
    host.hass = hass;
  });
  if (variant.startsWith("replacement")) {
    await panel.evaluate(host => {
      window.replacedRestoreSource = host;
      const parent = host.parentElement, hass = host.hass;
      host.remove();
      const next = document.createElement("extended-openai-management-panel");
      parent.append(next);
      next.hass = hass;
    });
    await expect(panel.locator('[data-config="__title"]')).toBeVisible();
    await panel.evaluate(host => host._loadSection(true));
    if (variant === "replacement newer edits") {
      await panel.locator('[data-config="__title"]').fill("New destination draft");
      await panel.locator('[data-config="max_tokens"]').fill("888");
    }
  }
  if (variant === "failed refresh") await page.evaluate(() => {window.failRestoreRead = true;});
  await page.evaluate(() => releaseRestoreReply());
  await expect.poll(() => page.evaluate(() => (window.replacedRestoreSource || document.querySelector("extended-openai-management-panel"))._backupRestoreOperations.size)).toBe(0);
  if (variant === "failed refresh") {
    await expect(panel.getByRole("button", {name:"Retry", exact:true})).toBeVisible();
    expect(await page.evaluate(() => browserHarness.calls.filter(c => c.section === "configuration" && c.action === "save").length)).toBe(1);
    await page.evaluate(() => {window.failRestoreRead = false;});
    await panel.getByRole("button", {name:"Retry", exact:true}).click();
  }
  const expectedTitle = variant === "replacement newer edits" ? "New destination draft" : variant === "baseline reversion" ? "Persisted after backup" : variant === "assistant" ? "Other assistant draft" : ["authentication", "lost", "rejected"].includes(variant) ? "Pre-restore stale title" : "Jarvis";
  await expect(panel.locator('[data-config="__title"]')).toHaveValue(expectedTitle);
  if (["newer edits", "failed refresh"].includes(variant)) await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("888");
  if (variant === "baseline reversion") {
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Persisted after backup");
  }
  if (["unchanged", "replacement"].includes(variant)) {
    await panel.locator('[data-config="__title"]').fill("Deliberate next save");
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Deliberate next save");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).not.toBe(777);
  }
  if (["newer edits", "failed refresh", "replacement newer edits"].includes(variant)) {
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(888);
    expect(await page.evaluate(() => browserHarness.getState().configuration.title)).toBe(variant === "replacement newer edits" ? "New destination draft" : "Jarvis");
  }
  expect(await page.evaluate(() => restoreWrites)).toBe(variant === "rejected" ? 0 : 1);
  await expectHarnessClean(page, errors);
});

});
