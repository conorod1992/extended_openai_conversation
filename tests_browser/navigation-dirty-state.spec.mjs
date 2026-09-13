import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function beforeUnloadIsBlocked(page) {
  return page.evaluate(() => {
    const event = new Event("beforeunload", {cancelable: true});
    const dispatched = window.dispatchEvent(event);
    return event.defaultPrevented || dispatched === false;
  });
}

async function navigateSection(panel, section) {
  const select = panel.locator("#local-section");
  await select.selectOption(section, {force: true});
}

async function normalizeHarnessPromptBaseline(page) {
  await page.evaluate(() => {
    const panel = document.querySelector("extended-openai-management-panel");
    // Production configuration is normalized with an authoritative prompt
    // default. The deliberately minimal browser fixture omits that field, so
    // align its loaded baseline with the empty prompt value the fixture renders.
    panel._configData.config.prompt = "";
    panel._draft.prompt = "";
  });
}

test("dirty navigation follows changed destinations and clears when every value returns to baseline", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  await normalizeHarnessPromptBaseline(page);

  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toHaveValue("Jarvis");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await title.fill("Navigation draft");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveClass(/eoc-has-unsaved/);
  await expect(panel.locator('#local-section option[value="basics"]')).toHaveText(/Basics\s+•$/);
  expect(await beforeUnloadIsBlocked(page)).toBe(true);

  await navigateSection(panel, "prompt-context");
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/prompt-context$/);
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
  await panel.locator("#prompt-editor").fill("Temporary navigation prompt");
  await expect(panel.locator('#local-section option[value="basics"]')).toHaveText(/Basics\s+•$/);
  await expect(panel.locator('#local-section option[value="prompt-context"]')).toHaveText(/Prompt & context\s+•$/);

  await navigateSection(panel, "basics");
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await panel.locator('[data-config="__title"]').fill("Jarvis");
  await expect(panel.locator('#local-section option[value="basics"]')).toHaveText("Basics");
  await expect(panel.locator('#local-section option[value="prompt-context"]')).toHaveText(/Prompt & context\s+•$/);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  expect(await beforeUnloadIsBlocked(page)).toBe(true);

  await navigateSection(panel, "prompt-context");
  await panel.locator("#prompt-editor").fill("");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).not.toHaveClass(/eoc-has-unsaved/);
  await expect(panel.locator('#local-section option[value="basics"]')).toHaveText("Basics");
  await expect(panel.locator('#local-section option[value="prompt-context"]')).toHaveText("Prompt & context");
  expect(await beforeUnloadIsBlocked(page)).toBe(false);

  await expectHarnessClean(page, pageErrors);
});

test("leaving dirty configuration can be cancelled without losing the draft or discarded without persisting it", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));

  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Unsaved navigation title");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();

  await panel.locator('.top-nav button[data-page="overview"]').click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#confirm-title")).toHaveText("Discard unsaved changes?");
  await panel.locator("#confirm-cancel").click();

  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Unsaved navigation title");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Jarvis");

  await panel.locator('.top-nav button[data-page="overview"]').click();
  await acceptConfirmation(panel);
  await expect(page).toHaveURL(/\/extended-openai\/overview$/);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Jarvis");

  await panel.locator('.top-nav button[data-page="assistant"]').click();
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).not.toHaveClass(/eoc-has-unsaved/);

  await expectHarnessClean(page, pageErrors);
});
