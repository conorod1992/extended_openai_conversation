import {expect, test} from "@playwright/test";
import {acceptConfirmation, browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const toolYaml = (name, description) => browserToolYaml(description).replace("name: browser_tool", `name: ${name}`);

async function installFakeHaYamlEditor(page) {
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
    }

    customElements.define("ha-yaml-editor", FakeHaYamlEditor);
  });
}

const nativeName = (editor) => editor.evaluate((element) => element.lastSetValue?.spec?.name);
const nativeDescription = (editor) => editor.evaluate((element) => element.lastSetValue?.spec?.description);

async function waitForNativeTool(editor, name) {
  await expect.poll(() => nativeName(editor)).toBe(name);
}

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.clear());
  await installFakeHaYamlEditor(page);
});

test("native YAML editor validates, duplicates, isolates tool state, deletes, and fits a narrow viewport", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.setViewportSize({width: 480, height: 800});
  await page.goto(fixtureUrl("capabilities/functions"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();

  // Keep this browser test focused on the shipped frontend lifecycle. The simple
  // browser backend stores YAML by persisted name, whereas the real backend can
  // serialize the supplied unsaved duplicate object. Mirror that one contract for
  // the duplicate dialog so the UI path is exercised faithfully.
  await panel.evaluate((element) => {
    const originalCall = element._call.bind(element);
    element._call = async (section, action, payload) => {
      if (section === "tools" && action === "serialize" && payload?.tool?.spec?.name?.endsWith("_copy")) {
        const tool = payload.tool;
        return {yaml: `spec:\n  name: ${tool.spec.name}\n  description: ${tool.spec.description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: ${tool.function.type}\n  name: get_user_from_user_id\n`};
      }
      return originalCall(section, action, payload);
    };
  });

  await panel.locator("#add-tool").click();
  let nativeEditor = panel.locator("#tool-yaml-native");
  await waitForNativeTool(nativeEditor, "browser_tool");
  await expect(nativeEditor).toHaveAttribute("aria-describedby", "tool-error");

  const bounds = await nativeEditor.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(480);

  await nativeEditor.evaluate((element, yaml) => element.setYamlForTest(yaml), toolYaml("browser_tool", "Validated original tool"));
  await panel.locator("#tool-validate").click();
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
  await expect(panel.locator("#tool-error")).toContainText("Name: browser_tool");
  await expect(panel.locator("#tool-error")).toContainText("Type: native");
  await panel.locator("#tool-save").click();

  let original = panel.locator(".tool-card").filter({hasText: "browser_tool"}).filter({hasText: "Validated original tool"});
  await expect(original).toBeVisible();

  await original.locator(".duplicate-tool").click();
  nativeEditor = panel.locator("#tool-yaml-native");
  await waitForNativeTool(nativeEditor, "browser_tool_copy");
  await expect.poll(() => nativeDescription(nativeEditor)).toBe("Validated original tool");
  await nativeEditor.evaluate((element, yaml) => element.setYamlForTest(yaml), toolYaml("browser_tool_copy", "Independent duplicate tool"));
  await panel.locator("#tool-save").click();

  original = panel.locator(".tool-card").filter({hasText: "browser_tool"}).filter({hasText: "Validated original tool"});
  let copy = panel.locator(".tool-card").filter({hasText: "browser_tool_copy"});
  await expect(original).toBeVisible();
  await expect(copy).toContainText("Independent duplicate tool");

  // Reopen each tool in turn. The native editor must hydrate from that tool's
  // serialized YAML rather than retaining the previously-opened document.
  await original.locator(".edit-tool").click();
  nativeEditor = panel.locator("#tool-yaml-native");
  await waitForNativeTool(nativeEditor, "browser_tool");
  await expect.poll(() => nativeDescription(nativeEditor)).toBe("Validated original tool");
  await panel.locator("#tool-cancel").click();

  copy = panel.locator(".tool-card").filter({hasText: "browser_tool_copy"});
  await copy.locator(".edit-tool").click();
  nativeEditor = panel.locator("#tool-yaml-native");
  await waitForNativeTool(nativeEditor, "browser_tool_copy");
  await expect.poll(() => nativeDescription(nativeEditor)).toBe("Independent duplicate tool");
  await panel.locator("#tool-cancel").click();

  await copy.locator(".delete-tool").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool_copy"})).toHaveCount(0);

  original = panel.locator(".tool-card").filter({hasText: "browser_tool"}).filter({hasText: "Validated original tool"});
  await original.locator(".delete-tool").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".tool-card").filter({hasText: "Validated original tool"})).toHaveCount(0);

  await expectHarnessClean(page, pageErrors);
});

test("modified native YAML requires confirmation before built-in preset replacement", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  const presetYaml = toolYaml("browser_tool", "Protected built-in replacement");

  await panel.evaluate((element, yaml) => {
    const originalCall = element._call.bind(element);
    element._call = async (section, action, payload) => {
      if (section === "tools" && action === "built_in_catalog") {
        return {functions: [{implementation: "protected_preset", label: "Protected preset", yaml, already_configured: false}]};
      }
      return originalCall(section, action, payload);
    };
  }, presetYaml);

  await panel.locator("#add-tool").click();
  const nativeEditor = panel.locator("#tool-yaml-native");
  await waitForNativeTool(nativeEditor, "browser_tool");

  const unsavedYaml = toolYaml("browser_tool", "Unsaved custom YAML");
  await nativeEditor.evaluate((element, yaml) => element.setYamlForTest(yaml), unsavedYaml);
  await panel.locator("#built-in-function").selectOption("protected_preset");

  const confirm = panel.locator("#confirm-dialog");
  await expect(confirm).toHaveJSProperty("open", true);
  await expect(confirm).toContainText("Replace current YAML with this built-in function preset?");
  await acceptConfirmation(panel);
  await expect.poll(() => nativeDescription(nativeEditor)).toBe("Protected built-in replacement");
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);

  await expectHarnessClean(page, pageErrors);
});
