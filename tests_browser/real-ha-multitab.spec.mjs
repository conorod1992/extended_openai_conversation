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

  // Both tabs loaded the same backend revision. Tab A wins the first write and
  // receives the new revision returned by the genuine HA management API. Use a
  // per-run title so an interrupted previous run cannot make this save a no-op.
  await titleA.fill(winnerTitle);
  await saveConfig(panelA);
  await expect(panelA.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(titleA).toHaveValue(winnerTitle);

  // Prove this is genuinely a stale-writer scenario before Tab B attempts its
  // save: A has advanced while B still holds the original loaded revision.
  const winnerRevision = await panelA.evaluate((element) => element._configData?.revision);
  const staleRevision = await panelB.evaluate((element) => element._configData?.revision);
  expect(winnerRevision).toBeDefined();
  expect(winnerRevision).not.toBe(baselineRevisionA);
  expect(staleRevision).toBe(baselineRevisionB);

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

  // A third independently loaded page proves the backend retained the winner and
  // did not partially apply any stale fields from Tab B.
  const pageC = await context.newPage();
  const pageErrorsC = trackPageErrors(pageC);
  let panelC = await openConfig(pageC);
  await expect(panelC.locator('[data-config="__title"]')).toHaveValue(winnerTitle);

  // Restore the original title using Tab A, whose successful write advanced its
  // revision. This also proves the rejected stale write did not wedge future saves.
  await titleA.fill(baselineTitle);
  await saveConfig(panelA);
  await expect(panelA.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await pageC.reload();
  panelC = pageC.locator("extended-openai-management-panel");
  await expect(panelC.locator('[data-config="__title"]')).toHaveValue(baselineTitle);

  await expectHarnessClean(page, pageErrorsA);
  await expectHarnessClean(pageB, pageErrorsB);
  await expectHarnessClean(pageC, pageErrorsC);
  await pageB.close();
  await pageC.close();
});
