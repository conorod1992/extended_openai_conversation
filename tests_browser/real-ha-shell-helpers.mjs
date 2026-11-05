import {expect} from "@playwright/test";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

export async function selectNativePickerOption(picker, label) {
  // New HA exposes option semantics; older releases put a button in the item.
  // Both paths still operate the real native control and its value events.
  const option = picker.getByRole("option").filter({hasText:label});
  const legacy = picker.locator("ha-combo-box-item").filter({hasText:label}).locator("button");
  const item = option.or(legacy).first();
  await item.waitFor({state:"visible"});
  const combo = picker.locator("ha-picker-combo-box");
  if (await combo.count()) {
    // HA 2026.11 renders rows to size its popup before wa-after-show. Its
    // subsequent search focus can scroll/reposition those still-visible rows.
    // Older HA renders them after opening and has no public shown property.
    if (await combo.evaluate(element => "shown" in element)) {
      await expect(combo).toHaveJSProperty("shown", true);
    }
    await expect.poll(() => combo.evaluate(element => element.matches(":focus-within"))).toBe(true);
  }
  await item.click();
}

export async function openColdHaRoute(context, page, route) {
  const baseUrl = process.env.REAL_HA_FRONTEND_URL;
  const auth = JSON.parse(process.env.REAL_HA_FRONTEND_AUTH);
  await context.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), auth);
  await page.goto(`${baseUrl}/extended-openai/${route}`, {waitUntil: "domcontentloaded"});
  const confirm = page.getByRole("button", {name: "Confirm", exact: true});
  if (await confirm.waitFor({state: "visible", timeout: 2000}).then(() => true).catch(() => false)) {
    await confirm.click();
    await page.goto(`${baseUrl}/extended-openai/${route}`, {waitUntil: "domcontentloaded"});
  }
  await expect(page.locator("home-assistant")).toHaveCount(1);
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toBeEnabled({timeout: 30000});
  await waitForManagementRouteReady(page, {name: route, path: route}, 30000);
  return panel;
}

export async function replaceNativeYaml(page, editor, yaml) {
  await expect(editor).toBeVisible({timeout: 30000});
  // Native activation validates and installs the initial document asynchronously.
  // Editing its empty CodeMirror placeholder can race the real initial setValue.
  await expect.poll(() => editor.evaluate(element => element.yaml?.trim().length || 0)).toBeGreaterThan(0);
  await editor.evaluate(async element => {
    await element.updateComplete;
    await element.shadowRoot.querySelector("ha-code-editor")?.updateComplete;
  });
  const surface = editor.locator('[contenteditable="true"], textarea').first();
  await surface.click();
  await expect.poll(() => editor.evaluate(element => Boolean(element.shadowRoot.querySelector("ha-code-editor")?.codemirror?.hasFocus))).toBe(true);
  const selectedAll = () => editor.evaluate(element => {
    const view = element.shadowRoot.querySelector("ha-code-editor")?.codemirror;
    return Boolean(view?.hasFocus && view.state.selection.main.from === 0
      && view.state.selection.main.to === view.state.doc.length);
  });
  await surface.press("ControlOrMeta+A");
  if (!await selectedAll()) await surface.press("Meta+A");
  await expect.poll(selectedAll).toBe(true);
  await page.keyboard.insertText(yaml);
  await expect.poll(() => editor.evaluate(element => element.yaml)).toBe(yaml);
}
