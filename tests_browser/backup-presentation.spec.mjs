import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const mobile of [false, true]) {
  test(`Backup choices explain the active mode and preserve accessible controls (bundle: ${bundled}, mobile: ${mobile})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.setViewportSize({width:mobile ? 390 : 1280, height:900});
    await page.goto(fixtureUrl("usage-maintenance/backup-restore", bundled ? "&bundle=1" : ""));
    if (mobile) await page.addStyleTag({content:":root {--primary-text-color:#eee;--secondary-text-color:#bbb;--primary-background-color:#111;--secondary-background-color:#292929;--card-background-color:#222;--divider-color:#555}"});
    const panel = page.locator("extended-openai-management-panel");
    const mode = panel.getByRole("combobox", {name:"Export type", exact:true});
    const toggle = panel.getByRole("switch", {name:"Include custom content", exact:true});
    const description = panel.locator("#transfer-export-description");
    const customWarning = panel.locator("#transfer-custom-warning");
    const privateWarning = panel.locator("#transfer-private-warning");
    await expect(panel.locator("h1")).toHaveCount(1);
    await expect(panel.getByRole("heading", {name:"Export / Backup", exact:true})).toHaveCount(0);
    await expect(panel.getByRole("heading", {name:"Export type", level:2, exact:true})).toBeVisible();
    await expect(mode).toHaveAccessibleDescription(await description.textContent());
    await expect(toggle).toHaveAccessibleDescription("Include custom prompts, Function Tools and rules for review before download.");
    await expect(toggle).not.toBeChecked();
    await expect(customWarning).toBeHidden();
    await expect(privateWarning).toBeHidden();
    await expect(description).toContainText("designated credentials and custom content are excluded by default");
    await expect(panel.getByText("Selected sections replace rather than merge destination data.")).toBeVisible();

    // Exercise both the lightweight route and the activated transfer module.
    for (const activated of [false, true]) {
      if (activated) {
        await panel.locator("#create-backup-transfer").click();
        await expect(panel.locator("[data-artifact]")).toBeVisible();
        await expect(panel.locator("[data-summary]")).toContainText("must be added or completed separately");
        await expect(panel.locator("[data-summary]")).toContainText("Saved source configuration is unchanged");
        expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "setup_export").at(-1).data)).toEqual({});
        await panel.locator("[data-cancel]").click();
      }
      await toggle.focus();
      await page.keyboard.press("Space");
      await expect(toggle).toBeChecked();
      await expect(toggle).toBeFocused();
      await expect(customWarning).toBeVisible();
      await expect(customWarning).toContainText("may contain secrets");
      await expect(privateWarning).toBeHidden();
      for (const value of ["full", "custom"]) {
        await mode.selectOption(value);
        await expect(toggle).toBeHidden();
        await expect(customWarning).toBeHidden();
        await expect(privateWarning).toBeVisible();
        await expect(privateWarning).toContainText("not encrypted");
        await expect(privateWarning).toContainText("store it securely");
        await expect(panel.locator("#transfer-custom-options")).toBeVisible({visible:value === "custom"});
        await expect(description).toHaveText(value === "full"
          ? "Back up private configuration, credentials and all supported saved assistant data."
          : "Choose which private sections to back up.");
        if (value === "custom") {
          await expect(panel.locator(".transfer-custom-section:checked")).toHaveCount(8);
          await panel.locator('.transfer-custom-section[value="configuration"]').uncheck();
          await expect(privateWarning).toBeVisible();
          await panel.locator('.transfer-custom-section[value="configuration"]').check();
        }
      }
      await mode.selectOption("setup");
      await expect(toggle).toBeChecked();
      await expect(customWarning).toBeVisible();
      await expect(privateWarning).toBeHidden();
      await panel.locator("#transfer-custom-label").click();
      await expect(toggle).not.toBeChecked();
      await expect(customWarning).toBeHidden();
    }

    const layout = await panel.evaluate(host => {
      const root = host.shadowRoot;
      const input = root.querySelector("#transfer-reviewed-custom");
      const row = input.closest(".config-toggle");
      const label = root.querySelector("#transfer-custom-label");
      const track = row.querySelector(".switch-track").getBoundingClientRect();
      const rect = row.getBoundingClientRect();
      const primary = getComputedStyle(host).getPropertyValue("--primary-text-color").trim();
      const probe = document.createElement("span");
      probe.style.color = primary;
      root.append(probe);
      const normal = getComputedStyle(probe).color;
      probe.remove();
      return {
        labelNormal:getComputedStyle(label).color === normal,
        taglineNormal:getComputedStyle(root.querySelector(".backup-panel .section-heading p")).color === normal,
        labelBeforeSwitch:label.getBoundingClientRect().right <= track.left,
        centered:Math.abs((track.top + track.bottom) / 2 - (rect.top + rect.bottom) / 2) < 1,
        switchSize:[track.width, track.height],
        overflow:document.documentElement.scrollWidth > innerWidth,
      };
    });
    expect(layout).toEqual({labelNormal:true, taglineNormal:true, labelBeforeSwitch:true, centered:true, switchSize:[44,26], overflow:false});
    await expectHarnessClean(page, errors);
  });
}

test("Custom-content toggle keeps the reviewed export payload and acknowledgement", async ({page}) => {
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    browserHarness.hass.callWS = async message => {
      const result = await original(message);
      return message.action === "setup_export" ? {...result, security:{review_required:Boolean(message.data.reviewed_custom), omitted:[]}} : result;
    };
  });
  await panel.getByRole("switch", {name:"Include custom content", exact:true}).check();
  await panel.locator("#create-backup-transfer").click();
  await expect(panel.locator("[data-ack]")).toBeVisible();
  await expect(panel.locator("[data-download]")).toBeDisabled();
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "setup_export").at(-1).data)).toEqual({reviewed_custom:true});
  await panel.locator("[data-ack]").check();
  await expect(panel.locator("[data-download]")).toBeEnabled();
  await panel.locator("[data-cancel]").click();
});
