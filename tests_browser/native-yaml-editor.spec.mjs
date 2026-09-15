import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Function Tool YAML uses the Home Assistant editor when it is registered", async ({page}) => {
  const pageErrors = trackPageErrors(page);
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
    customElements.define("ha-yaml-editor", FakeHaYamlEditor);
  });

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
  await expect(nativeEditor).toHaveJSProperty("lastSetValue", expect.objectContaining({spec: expect.objectContaining({name: "browser_tool"})}));

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
