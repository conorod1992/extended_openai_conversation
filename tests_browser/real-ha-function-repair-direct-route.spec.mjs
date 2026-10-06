import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the genuine Home Assistant management bridge");
const fixture = "/tests_browser/real-ha-fixture.html?route=capabilities%2Ffunctions&backend=" + encodeURIComponent(backendUrl);

test("cold Functions route exposes and repairs a quarantined tool", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = page.locator("extended-openai-management-panel");

  await page.goto(fixture);
  await expect(panel.locator(".tool-card-invalid")).toHaveCount(1);
  await expect(panel.locator(".tool-card-invalid")).toContainText("generated_invalid");
  await expect(panel.getByRole("heading", {name: "generated_valid_sibling", exact: true})).toBeVisible();

  const repair = await page.evaluate(() => window.browserHarness.panel._call("function_repair", "get"));
  expect(repair.invalid_tools).toHaveLength(1);
  const replacement = structuredClone(repair.invalid_tools[0].tool);
  replacement.spec.name = "generated_repaired";
  delete replacement.spec.parameters.description;

  await panel.locator(".edit-invalid-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#tool-yaml").fill(JSON.stringify(replacement, null, 2));
  await panel.locator("#tool-save").click();

  await expect(panel.locator(".tool-card-invalid")).toHaveCount(0);
  const saved = await page.evaluate(() => window.browserHarness.panel._call("configuration", "get"));
  expect(saved.config.functions.map((tool) => tool.spec.name)).toEqual(
    expect.arrayContaining(["generated_repaired", "generated_valid_sibling"]),
  );
  expect(saved.function_repair).toBeUndefined();
  expect(saved.config.functions.find((tool) => tool.spec.name === "generated_valid_sibling").enabled).toBe(false);

  // The bridge fixture rewrites its URL to the panel route; reopen its entry
  // point to simulate a fresh document without requiring an HA shell server.
  await page.goto(fixture);
  await expect(panel.getByRole("heading", {name: "generated_repaired", exact: true})).toBeVisible();
  await expectHarnessClean(page, errors);
});
