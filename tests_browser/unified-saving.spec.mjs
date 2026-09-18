import {expect, test} from "@playwright/test";
import {fixtureUrl, acceptConfirmation, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const unload = (page) => page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})));
const panelFor = (page) => page.locator("extended-openai-management-panel");
async function rejectOnce(page, section, action) {
  await page.evaluate(({section, action}) => {
    const hass = window.browserHarness.hass, original = hass.callWS.bind(hass);
    let rejected = false;
    hass.callWS = async (request) => {
      if (!rejected && request.section === section && request.action === action) { rejected = true; throw Error("Save rejected for regression test"); }
      return original(request);
    };
  }, {section, action});
}

for (const [view, field, section, action, removed] of [
  ["capabilities/guest-mode", "#guest-controls-enabled", "guest_mode", "save_policy", "#guest-policy-save"],
  ["capabilities/quiet-hours", "#qh-enabled", "quiet_hours", "update", "#qh-save,#qh-reset"],
]) {
  test(`${view}: shared bar, exact dirty state, discard, protected navigation and failed save`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(view)); const panel = panelFor(page);
    const control = panel.locator(field);
    // Guest controls intentionally live under Advanced.
    await control.evaluate((input) => { for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true; });
    await expect(panel.locator(removed)).toHaveCount(0);
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await control.check(); await expect(panel.locator("#save-page")).toHaveText("Save changes");
    expect(await unload(page)).toBe(true);
    await expect(panel.locator('.top-nav button[data-page="capabilities"]')).toHaveClass(/eoc-has-unsaved/);
    await control.uncheck(); await expect(panel.locator(".save-bar")).toHaveCount(0); expect(await unload(page)).toBe(false);
    await control.check();
    await panel.locator('.top-nav button[data-page="overview"]').click();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#confirm-cancel").click(); await expect(control).toBeChecked();
    await panel.locator("#discard-page").click(); await expect(control).not.toBeChecked(); expect(await unload(page)).toBe(false);
    await control.evaluate((input) => { for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true; });
    await control.check(); await rejectOnce(page, section, action);
    await panel.locator("#save-page").click();
    await expect(panel.locator("#toast")).toContainText("Save rejected");
    await expect(control).toBeChecked(); await expect(panel.locator("#save-page")).toBeEnabled(); expect(await unload(page)).toBe(true);
    await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0); expect(await unload(page)).toBe(false);
    await control.uncheck(); await panel.locator('.top-nav button[data-page="overview"]').click(); await acceptConfirmation(panel);
    await expect(page).toHaveURL(/overview$/);
    expect(await page.evaluate((section) => window.browserHarness.calls.filter((call) => call.section === section && ["update", "save_policy"].includes(call.action)).length, section)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

test("Request Rules settings coordinate partial saves and retain drafts through search and immediate toggles", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules")); const panel = panelFor(page);
  await expect(panel.locator(".rule-settings details")).toHaveCount(2);
  await panel.locator(".rule-settings details").evaluateAll((details) => details.forEach((item) => { item.open = true; }));
  await panel.locator("#rules-default-word-forms").uncheck();
  await panel.locator("#wording-add").click();
  await panel.locator(".wording-canonical").fill("turn on"); await panel.locator(".wording-alternatives").fill("enable, switch on");
  await panel.locator("#rule-search").fill("baseline"); await expect(panel.locator("#save-page")).toBeVisible();
  await rejectOnce(page, "request_rules", "wording_groups");
  await panel.locator("#save-page").click();
  await expect(panel.locator("#toast")).toContainText("Some settings were saved");
  await expect(panel.locator(".wording-canonical")).toHaveValue("turn on");
  await expect(panel.locator("#save-page")).toBeEnabled();
  await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0);
  expect(await page.evaluate(() => window.browserHarness.getState().requestRules.wording_groups)).toEqual([{canonical: "turn on", alternatives: ["enable", "switch on"]}]);
  await panel.locator("#rules-default-word-forms").check();
  await panel.locator(".rule-enabled").uncheck();
  await expect.poll(() => page.evaluate(() => window.browserHarness.getState().requestRules.rules[0].enabled)).toBe(false);
  await expect(panel.locator("#save-page")).toBeVisible();
  await panel.locator("#discard-page").click();
  await expect(panel.locator("#rules-default-word-forms")).not.toBeChecked();
  await expect(panel.locator(".rule-enabled")).not.toBeChecked();
  await expectHarnessClean(page, errors);
});

test("Request Rule dialog guards X/Escape and unload, while Cancel deliberately discards", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules")); const panel = panelFor(page), dialog = panel.locator("#rule-dialog");
  await panel.locator(".rule-edit").click(); await page.keyboard.press("Escape"); await expect(dialog).not.toBeVisible();
  await panel.locator(".rule-edit").click(); await panel.locator("#rule-name").fill("Changed rule"); expect(await unload(page)).toBe(true);
  await dialog.locator(".icon.rule-close").click(); await panel.locator("#confirm-cancel").click(); await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape"); await acceptConfirmation(panel); await expect(dialog).not.toBeVisible(); expect(await unload(page)).toBe(false);
  await panel.locator(".rule-edit").click(); await panel.locator("#rule-name").fill("Changed again");
  await rejectOnce(page, "request_rules", "update"); await dialog.getByRole("button", {name: "Save", exact: true}).click();
  await expect(panel.locator("#rule-error")).toContainText("Save rejected"); await expect(panel.locator("#rule-name")).toHaveValue("Changed again");
  await dialog.getByRole("button", {name: "Cancel", exact: true}).click(); await expect(dialog).not.toBeVisible();
  await expect(panel.locator("#confirm-dialog")).not.toBeVisible(); expect(await unload(page)).toBe(false);
  await expectHarnessClean(page, errors);
});
