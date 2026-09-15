import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test.beforeEach(async ({page}) => {
  // The browser fixture deliberately persists state in localStorage so other
  // suites can exercise reloads. These tests need an isolated baseline because
  // they intentionally validate/cancel/reopen the same Function Tool YAML.
  await page.addInitScript(() => localStorage.clear());
});

async function installFakeHaYamlEditor(page, {defineImmediately = true} = {}) {
  await page.addInitScript(({defineImmediately}) => {
    class FakeHaYamlEditor extends HTMLElement {
      constructor() {
        super();
        this._yaml = "";
        this.isValid = true;
        this.setValueCalls = 0;
      }

      setValue(value) {
        this.setValueCalls += 1;
        this.lastSetValue = structuredClone(value);
      }

      get yaml() {
        return this._yaml;
      }

      focus() {
        this.dataset.focused = "true";
      }

      setYamlForTest(value, isValid = true, errorMsg = "") {
        this._yaml = value;
        this.isValid = isValid;
        this.dispatchEvent(new CustomEvent("value-changed", {
          bubbles: true,
          composed: true,
          detail: {value: {}, isValid, errorMsg},
        }));
      }

      saveForTest() {
        this.dispatchEvent(new CustomEvent("editor-save", {bubbles: true, composed: true}));
      }
    }

    window.defineFakeHaYamlEditor = () => {
      if (!customElements.get("ha-yaml-editor")) customElements.define("ha-yaml-editor", FakeHaYamlEditor);
    };
    if (defineImmediately) window.defineFakeHaYamlEditor();
  }, {defineImmediately});
}

const toolSaveCalls = (page) => page.evaluate(() => window.browserHarness.calls.filter((call) => call.section === "tools" && call.action === "save"));

test("Function Tool YAML uses the Home Assistant editor when it is registered", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await installFakeHaYamlEditor(page);

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  await panel.locator("#add-tool").click();

  const dialog = panel.locator("#tool-dialog");
  const nativeEditor = panel.locator("#tool-yaml-native");
  const fallback = panel.locator("#tool-yaml");
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(nativeEditor).toBeVisible();
  await expect(fallback).toBeHidden();
  await expect(nativeEditor).toHaveAttribute("in-dialog", "");

  const yaml = browserToolYaml("Native Home Assistant YAML editor");
  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), yaml);
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");

  await nativeEditor.evaluate((element) => element.saveForTest());
  const card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Native Home Assistant YAML editor");

  await card.locator(".edit-tool").click();
  await expect(nativeEditor).toBeVisible();
  await expect(fallback).toBeHidden();
  await expect.poll(() => nativeEditor.evaluate((element) => element.lastSetValue?.spec?.name)).toBe("browser_tool");
  await expect(nativeEditor).toHaveJSProperty("inDialog", true);

  await expectHarnessClean(page, pageErrors);
});

test("Function Tool YAML activates after Home Assistant defines the editor late", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await installFakeHaYamlEditor(page, {defineImmediately: false});

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-tool").click();
  const nativeEditor = panel.locator("#tool-yaml-native");
  const fallback = panel.locator("#tool-yaml");

  await expect(fallback).toBeVisible();
  await expect(nativeEditor).toBeHidden();
  await page.evaluate(() => window.defineFakeHaYamlEditor());
  await expect(nativeEditor).toBeVisible();
  await expect(fallback).toBeHidden();
  await expect.poll(() => nativeEditor.evaluate((element) => element.lastSetValue?.spec?.name)).toBe("browser_tool");

  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), browserToolYaml("Late native registration"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Late native registration");
  await expectHarnessClean(page, pageErrors);
});

test("native YAML validation errors are surfaced and a valid edit can recover and save", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await installFakeHaYamlEditor(page);

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-tool").click();
  const nativeEditor = panel.locator("#tool-yaml-native");
  const status = panel.locator("#tool-error");

  await nativeEditor.evaluate((element) => element.setYamlForTest("spec:\n  description: broken", false, "Missing required spec.name"));
  await expect(status).toHaveClass(/invalid/);
  await expect(status).toContainText("Missing required spec.name");
  expect(await toolSaveCalls(page)).toHaveLength(0);

  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), browserToolYaml("Recovered from invalid YAML"));
  await expect(status).toContainText("YAML changed");
  await panel.locator("#tool-save").click();
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Recovered from invalid YAML");
  expect(await toolSaveCalls(page)).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});

test("native editor save shortcut respects dialog lifecycle and does not duplicate saves after reopen", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await installFakeHaYamlEditor(page);

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-tool").click();
  let nativeEditor = panel.locator("#tool-yaml-native");
  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), browserToolYaml("First shortcut save"));
  await nativeEditor.evaluate((element) => element.saveForTest());
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("First shortcut save");
  expect(await toolSaveCalls(page)).toHaveLength(1);

  await panel.locator(".tool-card").filter({hasText: "browser_tool"}).locator(".edit-tool").click();
  nativeEditor = panel.locator("#tool-yaml-native");
  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), browserToolYaml("Cancelled shortcut edit"));
  await panel.locator("#tool-cancel").click();
  await nativeEditor.evaluate((element) => element.saveForTest());
  expect(await toolSaveCalls(page)).toHaveLength(1);

  await panel.locator(".tool-card").filter({hasText: "browser_tool"}).locator(".edit-tool").click();
  nativeEditor = panel.locator("#tool-yaml-native");
  await nativeEditor.evaluate((element, value) => element.setYamlForTest(value), browserToolYaml("Second shortcut save"));
  await nativeEditor.evaluate((element) => element.saveForTest());
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Second shortcut save");
  expect(await toolSaveCalls(page)).toHaveLength(2);
  await expectHarnessClean(page, pageErrors);
});

test("Function Tool YAML falls back to the textarea if native editor initialisation fails", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await installFakeHaYamlEditor(page);

  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.evaluate((element) => {
    const originalCall = element._call.bind(element);
    let failOnce = true;
    element._call = async (section, action, payload) => {
      if (failOnce && section === "tools" && action === "validate_yaml") {
        failOnce = false;
        throw new Error("Native editor initialisation fixture failure");
      }
      return originalCall(section, action, payload);
    };
  });
  await panel.locator("#add-tool").click();

  await expect(panel.locator("#tool-yaml-native")).toBeHidden();
  const fallback = panel.locator("#tool-yaml");
  await expect(fallback).toBeVisible();
  await fallback.fill(browserToolYaml("Fallback after native init failure"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Fallback after native init failure");
  await expectHarnessClean(page, pageErrors);
});

test("Function Tool YAML keeps the textarea fallback when the HA editor is unavailable", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-tool").click();

  await expect(panel.locator("#tool-yaml-native")).toBeHidden();
  await expect(panel.locator("#tool-yaml")).toBeVisible();
  await panel.locator("#tool-yaml").fill(browserToolYaml("Textarea fallback"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Textarea fallback");

  await expectHarnessClean(page, pageErrors);
});
