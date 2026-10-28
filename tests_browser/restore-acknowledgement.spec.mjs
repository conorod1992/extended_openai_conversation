import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundle of [false, true]) for (const fault of ["cache", "modal", "lost", "malformed", "provider warning"]) {
  test(`restore retains its outcome through ${fault} (${bundle ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("usage-maintenance/backup-restore", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator("#transfer-export-mode").selectOption("full");
    const downloading = page.waitForEvent("download");
    await panel.locator("#create-backup-transfer").click();
    const path = await (await downloading).path();
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await panel.locator('[data-config="__title"]').fill("Changed after backup");
    await panel.getByRole("button", {name:"Save changes", exact:true}).click();
    await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Changed after backup");
    await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
    await panel.locator("#backup-file-transfer").setInputFiles(path);
    await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
    await panel.evaluate((host, fault) => {
      window.restoreWrites = 0;
      window.restoreNotices = [];
      const send = browserHarness.hass.callWS.bind(browserHarness.hass);
      browserHarness.hass.callWS = async message => {
        const result = await send(message);
        if (message.action !== "import_restore") return result;
        restoreWrites += 1;
        if (fault === "lost") throw {type:"result", success:false, error:{code:3, message:"Connection lost"}};
        if (fault === "malformed") return {status:"pending"};
        return fault === "provider warning" ? {...result, provider_connection_message:"Provider connection was not changed"} : result;
      };
      const toast = host._toast.bind(host);
      host._toast = (message, warning) => { restoreNotices.push({message, warning}); toast(message, warning); };
      if (fault === "cache") {
        const invalidate = host._invalidateAfterMutation.bind(host);
        host._invalidateAfterMutation = (id, section, action) => {
          if (section === "backup" && action === "restore") throw new Error("cache failed after restore");
          return invalidate(id, section, action);
        };
      }
      if (fault === "modal") host.shadowRoot.querySelector("#restore-dialog").close = () => { throw new Error("modal failed after restore"); };
    }, fault);
    await panel.locator("#restore-transfer-apply").click();
    await acceptConfirmation(panel);
    await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Jarvis");
    await expect.poll(() => panel.evaluate(host => host._backupRestoreOperations.size)).toBe(0);
    expect(await panel.evaluate(host => host._backupTransferSession)).toBeNull();
    expect(await page.evaluate(() => restoreWrites)).toBe(1);
    const notices = await page.evaluate(() => restoreNotices);
    if (["lost", "malformed"].includes(fault)) {
      expect(notices.some(item => item.message.includes("Selected sections restored"))).toBe(false);
      expect(notices.some(item => item.message.includes("unknown") && item.warning)).toBe(true);
      await expect(panel.locator("#restore-transfer-apply")).toBeDisabled();
    } else {
      expect(notices.some(item => item.message.includes("Selected sections restored"))).toBe(true);
      expect(notices.some(item => item.message.includes("Unable to restore"))).toBe(false);
      if (fault === "provider warning") expect(notices.some(item => item.message.includes("Provider connection was not changed") && item.warning)).toBe(true);
      await panel.evaluate(host => host.shadowRoot.querySelector("#restore-transfer-apply")?.click());
      expect(await page.evaluate(() => restoreWrites)).toBe(1);
    }
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) test(`restore confirmation cannot cross an authentication round trip (${bundle ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore", bundle ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloading = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const path = await (await downloading).path();
  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await page.evaluate(() => {
    window.restoreWrites = 0;
    const send = browserHarness.hass.callWS.bind(browserHarness.hass);
    browserHarness.hass.callWS = async message => {
      const result = await send(message);
      if (message.action === "import_restore") restoreWrites += 1;
      return result;
    };
  });
  await panel.locator("#restore-transfer-apply").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.evaluate(host => {
    const hass = host.hass;
    host.hass = {...hass, user:{...hass.user, id:"intervening-authentication"}};
    host.hass = hass;
  });
  await acceptConfirmation(panel);
  expect(await page.evaluate(() => restoreWrites)).toBe(0);
  expect(await panel.evaluate(host => host._backupTransferSession?.session_id)).toBeTruthy();
  // A fresh confirmation under the current owner remains usable.
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => restoreWrites)).toBe(1);
  await expectHarnessClean(page, errors);
});
