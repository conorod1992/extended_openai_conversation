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
    const description = panel.locator("#transfer-export-description");
    const privateWarning = panel.locator("#transfer-private-warning");
    await expect(panel.locator("h1")).toHaveCount(1);
    await expect(panel.getByRole("heading", {name:"Export / Backup", exact:true})).toHaveCount(0);
    await expect(panel.getByRole("heading", {name:"Export type", level:2, exact:true})).toBeVisible();
    await expect(mode).toHaveAccessibleDescription(await description.textContent());
    await expect(mode).toHaveValue("full");
    await expect(mode.locator("option")).toHaveText(["Full Backup (private)", "Custom (private)"]);
    await expect(privateWarning).toBeVisible();
    await expect(panel.getByText("Selected sections replace rather than merge destination data.")).toBeVisible();

    // Exercise both the lightweight route and the activated transfer module.
    for (const activated of [false, true]) {
      if (activated) {
        const download = page.waitForEvent("download");
        await panel.locator("#create-backup-transfer").click();
        expect((await download).suggestedFilename()).toMatch(/\.zip$/);
        expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "export_start").at(-1).data)).toEqual({mode:"full"});
      }
      for (const value of ["full", "custom"]) {
        await mode.selectOption(value);
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
      await mode.selectOption("full");
      await expect(description).toContainText("all supported saved assistant data");
    }

    const layout = await panel.evaluate(host => {
      const root = host.shadowRoot;
      const primary = getComputedStyle(host).getPropertyValue("--primary-text-color").trim();
      const probe = document.createElement("span");
      probe.style.color = primary;
      root.append(probe);
      const normal = getComputedStyle(probe).color;
      probe.remove();
      return {
        taglineNormal:getComputedStyle(root.querySelector(".backup-panel .section-heading p")).color === normal,
        overflow:document.documentElement.scrollWidth > innerWidth,
      };
    });
    await expectHarnessClean(page, errors);
  });
}
