import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function installFakeHaYamlEditor(page) {
  await page.addInitScript(() => {
    class FakeHaYamlEditor extends HTMLElement {
      constructor() {
        super();
        this._yaml = "";
        this.values = [];
      }

      setValue(value) {
        this.values.push(structuredClone(value));
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

const saveCalls = (page) => page.evaluate(() => window.browserHarness.calls.filter(
  (call) => call.section === "tools" && call.action === "save",
));

const toolCalls = (page, action) => page.evaluate((wantedAction) => window.browserHarness.calls.filter(
  (call) => call.section === "tools" && call.action === wantedAction,
), action);

const waitForTool = async (editor, name) => {
  await expect.poll(() => editor.evaluate((element) => element.lastSetValue?.spec?.name)).toBe(name);
};

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => localStorage.clear());
  await installFakeHaYamlEditor(page);
});

test("native Function Tool routes retain enable, config-check, and open-dialog agent-switch behavior", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");

  await panel.locator("#add-tool").click();
  let editor = panel.locator("#tool-yaml-native");
  await waitForTool(editor, "browser_tool");
  await editor.evaluate((element, yaml) => element.setYamlForTest(yaml), browserToolYaml("Route regression tool"));
  await panel.locator("#tool-save").click();

  let card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Route regression tool");
  const enabled = card.locator(".tool-enabled");
  await enabled.uncheck();
  await expect(enabled).not.toBeChecked();
  await enabled.check();
  await expect(enabled).toBeChecked();
  const enabledCalls = await toolCalls(page, "set_enabled");
  expect(enabledCalls.map((call) => call.enabled)).toEqual([false, true]);

  await panel.locator("#validate-tools").click();
  await expect(panel.locator("#tool-status")).toHaveClass(/valid/);
  await expect(panel.locator("#tool-status")).toContainText("All saved tools and groups are valid");

  // Make the harness expose a second assistant. Section calls can continue to use
  // the same deterministic backend because this assertion is about frontend
  // lifecycle/isolation while the dialog exists, not per-agent persistence.
  await panel.evaluate(async (element) => {
    const hass = window.browserHarness.hass;
    const originalCallWS = hass.callWS.bind(hass);
    const first = structuredClone(element._data.agents[0]);
    const second = {...structuredClone(first), subentry_id: "agent-2", title: "Friday"};
    hass.callWS = async (message) => {
      if (message.action === "agents" && !message.section) {
        return {is_admin: true, agents: [first, second], scopes: structuredClone(element._data.scopes || [])};
      }
      return originalCallWS(message);
    };
    await element._loadAgents("agent-1");
  });

  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await card.locator(".edit-tool").click();
  editor = panel.locator("#tool-yaml-native");
  await waitForTool(editor, "browser_tool");
  await editor.evaluate((element, yaml) => element.setYamlForTest(yaml), browserToolYaml("Unsaved before agent switch"));
  const savesBeforeSwitch = (await saveCalls(page)).length;

  await panel.locator("#agent").selectOption("agent-2");
  await expect(panel.locator("#agent")).toHaveValue("agent-2");
  await expect(panel.locator("#tool-dialog")).not.toHaveJSProperty("open", true);
  expect((await saveCalls(page)).length).toBe(savesBeforeSwitch);

  await panel.locator("#agent").selectOption("agent-1");
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Route regression tool");
  await expect(card).not.toContainText("Unsaved before agent switch");

  await expectHarnessClean(page, pageErrors);
});

test("native editor survives repeated dialog cycles, rapid edits, and large complex YAML", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");

  await panel.locator("#add-tool").click();
  let editor = panel.locator("#tool-yaml-native");
  await waitForTool(editor, "browser_tool");

  // Rapid successive native-editor events must leave the latest raw YAML as the
  // authoritative value used by validation/save.
  for (let index = 0; index < 20; index += 1) {
    await editor.evaluate(
      (element, yaml) => element.setYamlForTest(yaml),
      browserToolYaml(`Rapid edit ${index}`),
    );
  }
  await panel.locator("#tool-save").click();
  let card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Rapid edit 19");

  // Repeatedly open and cancel the same dialog. The native editor must hydrate the
  // persisted tool every time and must not accumulate save listeners/state.
  const savesBeforeCycles = (await saveCalls(page)).length;
  for (let index = 0; index < 6; index += 1) {
    card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
    await card.locator(".edit-tool").click();
    editor = panel.locator("#tool-yaml-native");
    await waitForTool(editor, "browser_tool");
    await expect.poll(() => editor.evaluate((element) => element.lastSetValue?.spec?.description)).toBe("Rapid edit 19");
    await panel.locator("#tool-cancel").click();
  }
  expect((await saveCalls(page)).length).toBe(savesBeforeCycles);

  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await card.locator(".edit-tool").click();
  editor = panel.locator("#tool-yaml-native");
  await waitForTool(editor, "browser_tool");

  const manyProperties = Array.from({length: 120}, (_, index) => `      property_${index}:\n        type: string\n        description: \"Value ${index} – café 🚀\"`).join("\n");
  const complexYaml = `# persisted top-level comment\nspec:\n  name: browser_tool\n  description: \"Unicode café 🚀 and quoted: value\"\n  parameters:\n    type: object\n    properties:\n${manyProperties}\nfunction:\n  type: native\n  name: get_user_from_user_id\n# trailing comment\n`;
  await editor.evaluate((element, yaml) => element.setYamlForTest(yaml), complexYaml);
  await panel.locator("#tool-save").click();

  const persistedYaml = await page.evaluate(() => window.browserHarness.getState().toolYamls.browser_tool);
  expect(persistedYaml).toBe(complexYaml);

  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await card.locator(".edit-tool").click();
  await expect(panel.locator("#tool-yaml")).toHaveValue(complexYaml);
  await panel.locator("#tool-cancel").click();

  await expectHarnessClean(page, pageErrors);
});
