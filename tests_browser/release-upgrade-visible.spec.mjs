import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const expectedTitle = process.env.UPGRADE_BROWSER_EXPECTED_TITLE || "Upgrade Acceptance Agent";
const expectedModel = process.env.UPGRADE_BROWSER_EXPECTED_MODEL || "gpt-5.6";
const savedTitle = process.env.UPGRADE_BROWSER_SAVED_TITLE || "Upgrade Acceptance Agent - Browser Saved";

test.skip(!backendUrl, "requires the browser-visible release-upgrade backend bridge");

const realFixtureUrl = (route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

test("migrated release settings render, save, and reload through the candidate UI", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("assistant/basics"));

  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  const model = panel.locator('[data-config="chat_model"]');

  await expect(title).toBeVisible();
  await expect(model).toBeVisible();
  await expect(title).toHaveValue(expectedTitle);
  await expect(model).toHaveValue(expectedModel);

  await title.fill(savedTitle);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  // The fixture page owns the browser-side call log. Capture the save before
  // navigating, because the reload below creates a fresh fixture/harness instance.
  const saveActions = await page.evaluate(() =>
    window.browserHarness.calls
      .filter((call) => call.section === "configuration")
      .map((call) => call.action),
  );
  expect(saveActions).toContain("get");
  expect(saveActions).toContain("save");

  await page.goto(realFixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue(savedTitle);
  await expect(panel.locator('[data-config="chat_model"]')).toHaveValue(expectedModel);
  await expect(panel.locator("#agent option:checked")).toHaveText(savedTitle);

  const reloadActions = await page.evaluate(() =>
    window.browserHarness.calls
      .filter((call) => call.section === "configuration")
      .map((call) => call.action),
  );
  expect(reloadActions).toContain("get");
  await expectHarnessClean(page, pageErrors);
});
