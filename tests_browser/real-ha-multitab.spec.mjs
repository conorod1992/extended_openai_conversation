import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

async function openConfig(page) {
  await page.goto(realFixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  return panel;
}

async function saveConfig(panel) {
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
}

async function configurationUpdateCount(page) {
  return page.evaluate(() => window.browserHarness.calls
    .filter((call) => call.section === "configuration" && call.action === "update")
    .length);
}

test("a stale second tab cannot overwrite a newer agent configuration", async ({context, page}) => {
  const pageErrorsA = trackPageErrors(page);
  const pageB = await context.newPage();
  const pageErrorsB = trackPageErrors(pageB);

  const panelA = await openConfig(page);
  const panelB = await openConfig(pageB);
  const titleA = panelA.locator('[data-config="__title"]');
  const titleB = panelB.locator('[data-config="__title"]');
  const baselineTitle = await titleA.inputValue();
  const runToken = Date.now().toString(36);
  const winnerTitle = `Browser multi-tab winner ${runToken}`;
  const staleDraftTitle = `Browser multi-tab stale ${runToken}`;
  const baselineRevisionA = await panelA.evaluate((element) => element._configData?.revision);
  const baselineRevisionB = await panelB.evaluate((element) => element._configData?.revision);
  expect(baselineRevisionA).toBeDefined();
  expect(baselineRevisionB).toBe(baselineRevisionA);
  await expect(titleB).toHaveValue(baselineTitle);

  // Both tabs loaded the same backend revision. Tab A wins the first write. Use a
  // per-run title so an interrupted previous run cannot make this save a no-op.
  await titleA.fill(winnerTitle);
  await saveConfig(panelA);
  await expect(panelA.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(titleA).toHaveValue(winnerTitle);

  // Read the authoritative post-save state through a freshly loaded third tab.
  // Do not couple the stale-writer proof to the exact moment Tab A's internal
  // _configData object is refreshed around its successful save/render lifecycle.
  const pageC = await context.newPage();
  const pageErrorsC = trackPageErrors(pageC);
  let panelC = await openConfig(pageC);
  const winnerRevision = await panelC.evaluate((element) => element._configData?.revision);
  const staleRevision = await panelB.evaluate((element) => element._configData?.revision);
  expect(winnerRevision).toBeDefined();
  expect(winnerRevision).not.toBe(baselineRevisionA);
  expect(staleRevision).toBe(baselineRevisionB);
  await expect(panelC.locator('[data-config="__title"]')).toHaveValue(winnerTitle);

  // Tab B's disjoint local draft must be rejected rather than replacing Tab A's
  // newer full configuration snapshot. Track the shipped harness call itself,
  // then wait for the save button to be re-enabled by the handler's finally path;
  // this proves the stale update attempt finished without coupling the test to
  // Playwright's cross-origin HTTP response event or HA's exact error payload.
  await titleB.fill(staleDraftTitle);
  await expect(panelB.getByText("Unsaved changes", {exact: true})).toBeVisible();
  const updateCountBefore = await configurationUpdateCount(pageB);
  const staleSaveButton = panelB.getByRole("button", {name: "Save configuration", exact: true});
  await staleSaveButton.click();
  await expect.poll(() => configurationUpdateCount(pageB)).toBe(updateCountBefore + 1);
  await expect(staleSaveButton).toBeEnabled();
  await expect(titleB).toHaveValue(staleDraftTitle);
  await expect(panelB.getByText("Unsaved changes", {exact: true})).toBeVisible();

  // Reload the fresh reader after the rejected write to prove the backend still
  // contains Tab A's winning state and did not partially apply Tab B's stale draft.
  panelC = await openConfig(pageC);
  await expect(panelC.locator('[data-config="__title"]')).toHaveValue(winnerTitle);
  await expect.poll(() => panelC.evaluate((element) => element._configData?.revision)).toBe(winnerRevision);

  // Restore the original title from the fresh tab, which necessarily owns the
  // current winning revision. This keeps cleanup independent of Tab A's render timing.
  const titleC = panelC.locator('[data-config="__title"]');
  await titleC.fill(baselineTitle);
  await saveConfig(panelC);
  await expect(panelC.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(titleC).toHaveValue(baselineTitle);

  // A fresh reload of the original page confirms cleanup reached the backend.
  const restoredPanelA = await openConfig(page);
  await expect(restoredPanelA.locator('[data-config="__title"]')).toHaveValue(baselineTitle);

  await expectHarnessClean(page, pageErrorsA);
  await expectHarnessClean(pageB, pageErrorsB);
  await expectHarnessClean(pageC, pageErrorsC);
  await pageB.close();
  await pageC.close();
});
