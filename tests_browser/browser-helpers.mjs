import {expect} from "@playwright/test";

export const fixtureUrl = (route, extra = "") => `/tests_browser/fixture.html?route=${route}${extra}`;
export function trackPageErrors(page) { const errors = []; page.on("pageerror", (error) => errors.push(error.message)); return errors; }
export async function expectHarnessClean(page, pageErrors) {
  const harness = await page.evaluate(() => ({errors: window.browserHarness?.windowErrors || [], rejections: window.browserHarness?.rejections || []}));
  expect(pageErrors).toEqual([]); expect(harness).toEqual({errors: [], rejections: []});
}
export async function acceptConfirmation(panel) {
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
}
export const browserToolYaml = (description = "Browser journey tool") => `spec:\n  name: browser_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: script\n  sequence: []\n`;
