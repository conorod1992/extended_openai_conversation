import {expect} from "@playwright/test";

export const fixtureUrl = (route, extra = "") => `/tests_browser/fixture.html?route=${route}${extra}${process.env.SHIPPED_BUNDLE === "1" ? "&bundle=1" : ""}`;

export function trackPageErrors(page) {
  const diagnostics = [];
  diagnostics.consoleErrors = [];
  diagnostics.requestFailures = [];
  diagnostics.badResponses = [];

  page.on("pageerror", (error) => diagnostics.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const location = message.location();
    const suffix = location?.url
      ? ` (${location.url}${location.lineNumber != null ? `:${location.lineNumber}` : ""})`
      : "";
    diagnostics.consoleErrors.push(`${message.text()}${suffix}`);
  });
  page.on("requestfailed", (request) => {
    diagnostics.requestFailures.push(
      `${request.method()} ${request.url()}: ${request.failure()?.errorText || "request failed"}`,
    );
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    diagnostics.badResponses.push(
      `${response.status()} ${response.request().method()} ${response.url()}`,
    );
  });
  return diagnostics;
}

export async function expectHarnessClean(page, diagnostics) {
  const harness = await page.evaluate(() => ({errors: window.browserHarness?.windowErrors || [], rejections: window.browserHarness?.rejections || []}));
  expect(diagnostics).toHaveLength(0);
  expect(diagnostics.consoleErrors || []).toEqual([]);
  expect(diagnostics.requestFailures || []).toEqual([]);
  expect(diagnostics.badResponses || []).toEqual([]);
  expect(harness).toEqual({errors: [], rejections: []});
}
export async function acceptConfirmation(panel) {
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
}
export async function openFunctionAddMenu(panel, action) {
  const item = panel.locator(action);
  if (!await item.isVisible()) await panel.locator("#function-add").click();
  await item.click();
}
export const browserToolYaml = (description = "Browser journey tool") => `spec:\n  name: browser_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: get_user_from_user_id\n`;


export async function exerciseConfigurationDraftTransition(page, marker) {
  const panel = page.locator("extended-openai-management-panel");
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await panel.locator('[data-config="prompt"]').fill(marker);
  const baseline = await page.evaluate(() => browserHarness.getState().configuration.config);
  await panel.evaluate(host => host._navigate("data-memory", "knowledge"));
  const enabled = baseline.knowledge_enabled === false;
  await panel.locator("#knowledge-enabled-toggle").setChecked(enabled);
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.knowledge_enabled)).toBe(enabled);
  expect(await panel.evaluate(host => host._draft.prompt)).toBe(marker);
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.prompt)).toBe(baseline.prompt);
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue(marker);
  // Inject one failed write at the existing fixture transport boundary.
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let failed = false;
    hass.callWS = request => {
      if (!failed && request.section === "configuration" && request.action === "save") {
        failed = true;
        return Promise.reject(new Error("Generated save failure"));
      }
      return original(request);
    };
  });
  await panel.locator("#save-config").click();
  await expect(panel.getByText("Unable to save configuration: Generated save failure", {exact: true})).toBeVisible();
  expect(await panel.evaluate(host => ({prompt:host._draft.prompt,dirty:host._configDirty}))).toEqual({prompt:marker,dirty:true});
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.prompt)).toBe(baseline.prompt);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.prompt)).toBe(marker);
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.knowledge_enabled)).toBe(enabled);
  await page.goto(fixtureUrl("assistant/prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue(marker);
}
