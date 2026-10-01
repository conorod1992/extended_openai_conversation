import {expect} from "@playwright/test";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

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
  const surface = editor.locator('[contenteditable="true"], textarea').first();
  await surface.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText(yaml);
  await expect.poll(() => editor.evaluate(element => element.yaml)).toBe(yaml);
}
