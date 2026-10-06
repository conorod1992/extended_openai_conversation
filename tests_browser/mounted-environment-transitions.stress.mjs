import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function openDirtyMemoryDialog(page) {
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill("Mounted environment draft");
  return panel;
}

test("mounted panel follows live light-dark theme changes without losing editor state", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDirtyMemoryDialog(page);

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.themes = {darkMode: false};
    document.documentElement.style.setProperty("--primary-background-color", "#ffffff");
    document.documentElement.style.setProperty("--secondary-background-color", "#f5f5f5");
    document.documentElement.style.setProperty("--primary-text-color", "#111111");
    browserHarness.panel.hass = hass;
  });
  await expect(panel).toHaveCSS("color-scheme", "light");
  const lightBackground = await panel.evaluate(host => getComputedStyle(host).backgroundColor);

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.themes = {darkMode: true};
    document.documentElement.style.setProperty("--primary-background-color", "#111111");
    document.documentElement.style.setProperty("--secondary-background-color", "#1c1c1c");
    document.documentElement.style.setProperty("--primary-text-color", "#f5f5f5");
    browserHarness.panel.hass = hass;
  });
  await expect(panel).toHaveCSS("color-scheme", "dark");
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#memory-content")).toHaveValue("Mounted environment draft");
  const darkBackground = await panel.evaluate(host => getComputedStyle(host).backgroundColor);
  expect(darkBackground).not.toBe(lightBackground);

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.themes = {darkMode: false};
    document.documentElement.style.setProperty("--primary-background-color", "#ffffff");
    document.documentElement.style.setProperty("--secondary-background-color", "#f5f5f5");
    document.documentElement.style.setProperty("--primary-text-color", "#111111");
    browserHarness.panel.hass = hass;
  });
  await expect(panel).toHaveCSS("color-scheme", "light");
  await expect(panel.locator("#memory-content")).toHaveValue("Mounted environment draft");
  await expectHarnessClean(page, errors);
});

test("open editor survives portrait-landscape-portrait viewport transitions", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width: 390, height: 844});
  const panel = await openDirtyMemoryDialog(page);
  const save = panel.locator("#memory-save");

  for (const viewport of [
    {width: 844, height: 390},
    {width: 768, height: 1024},
    {width: 390, height: 844},
  ]) {
    await page.setViewportSize(viewport);
    await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#memory-content")).toHaveValue("Mounted environment draft");
    await save.scrollIntoViewIfNeeded();
    await expect(save).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  }

  await panel.locator("#memory-content").fill("Mounted environment draft after resize");
  await save.click();
  await expect(panel.getByText("Mounted environment draft after resize", {exact: true})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("touch-first coarse-pointer context exposes all important actions without hover", async ({browser}) => {
  const context = await browser.newContext({
    hasTouch: true,
    viewport: {width: 390, height: 844},
  });
  const page = await context.newPage();
  const errors = trackPageErrors(page);
  try {
    await page.goto(fixtureUrl("capabilities/request-rules"));
    const panel = page.locator("extended-openai-management-panel");
    expect(await page.evaluate(() => matchMedia("(pointer: coarse)").matches)).toBe(true);
    expect(await page.evaluate(() => matchMedia("(hover: none)").matches)).toBe(true);

    const card = panel.locator(".request-rule-card").first();
    await expect(card).toBeVisible();
    await expect(card.locator(".rule-edit")).toBeVisible();
    await expect(card.locator(".rule-enabled")).toBeVisible();
    await expect(card.locator(".rule-move-menu > summary")).toBeVisible();

    await card.locator(".rule-edit").tap();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#rule-save")).toBeVisible();
    await panel.locator(".rule-close").first().tap();
    await expect(panel.locator("#rule-dialog")).not.toBeVisible();

    await panel.locator("#local-section").selectOption("functions");
    await expect(page).toHaveURL(/\/extended-openai\/capabilities\/functions$/);
    await expect(panel.locator("#function-add")).toBeVisible();
    await expectHarnessClean(page, errors);
  } finally {
    await context.close();
  }
});

test("mounted native HA controls receive live language metadata changes without losing draft state", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/voice"));
  const panel = page.locator("extended-openai-management-panel");
  const nativeControls = panel.locator("ha-selector, ha-entity-picker, ha-user-picker, ha-yaml-editor");
  await expect(nativeControls.first()).toBeAttached();

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.language = "en";
    document.documentElement.lang = "en";
    browserHarness.panel.hass = hass;
  });
  expect(await nativeControls.first().evaluate(element => element.hass?.language)).toBe("en");

  await panel.evaluate(host => host._navigate("assistant", "basics"));
  const title = panel.locator('[data-config="__title"]');
  await title.fill("Language transition draft");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();

  await panel.evaluate(host => host._navigate("assistant", "voice"));
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.language = "fr";
    document.documentElement.lang = "fr";
    browserHarness.panel.hass = hass;
  });
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  await expect(nativeControls.first()).toBeAttached();
  expect(await nativeControls.first().evaluate(element => element.hass?.language)).toBe("fr");

  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await expect(title).toHaveValue("Language transition draft");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("Function Tool editor falls back when native YAML lookup disappears and recovers when restored", async ({page}) => {
  const errors = trackPageErrors(page);

  await page.addInitScript(() => {
    class FakeHaYamlEditor extends HTMLElement {
      constructor() {
        super();
        this._yaml = "";
        this.isValid = true;
      }
      setValue(value) {
        this.lastSetValue = structuredClone(value);
      }
      get yaml() {
        return this._yaml;
      }
      focus() {}
    }
    customElements.define("ha-yaml-editor", FakeHaYamlEditor);
  });

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-yaml-native")).toBeVisible();
  await panel.locator("#tool-cancel").click();

  await page.evaluate(() => {
    const originalGet = customElements.get.bind(customElements);
    window.__eocOriginalCustomElementGet = originalGet;
    customElements.get = name => name === "ha-yaml-editor" ? undefined : originalGet(name);
  });
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-yaml")).toBeVisible();
  await expect(panel.locator("#tool-yaml-native")).toBeHidden();
  await panel.locator("#tool-yaml").fill("spec:\n  name: fallback_runtime\n  description: Runtime fallback\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: get_user_from_user_id\n");
  await panel.locator("#tool-cancel").click();

  await page.evaluate(() => {
    customElements.get = window.__eocOriginalCustomElementGet;
  });
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-yaml-native")).toBeVisible();
  await expect(panel.locator("#tool-yaml")).toBeHidden();
  await panel.locator("#tool-cancel").click();
  await expectHarnessClean(page, errors);
});
