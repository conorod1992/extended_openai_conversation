import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

test("Functions list paints before optional YAML and HA catalogue modules load", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  const loaded = await page.evaluate(() => performance.getEntriesByType("resource").map(entry => entry.name));
  expect(loaded.some(url => url.endsWith("/agent-config-native-yaml.js"))).toBe(false);
  expect(loaded.some(url => url.endsWith("/ha-llm-tools.js"))).toBe(false);

  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-yaml")).toBeEditable();
  await expect.poll(() => page.evaluate(() => performance.getEntriesByType("resource")
    .some(entry => entry.name.endsWith("/agent-config-native-yaml.js")))).toBe(true);
});
