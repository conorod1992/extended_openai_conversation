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

async function lastConfigurationUpdate(page) {
  return page.evaluate(() => [...window.browserHarness.calls]
    .reverse()
    .find((call) => call.section === "configuration" && call.action === "update") || null);
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

  // Both tabs loaded the same backend revision. Tab A wins the first write through
  // the shipped Save button. Prove that the real UI sends the revision it loaded,
  // rather than relying only on the eventual backend state.
  await titleA.fill(winnerTitle);
  await saveConfig(panelA);
  await expect(panelA.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(titleA).toHaveValue(winnerTitle);
  const winnerWrite = await lastConfigurationUpdate(page);
  expect(winnerWrite).not.toBeNull();
  expect(winnerWrite.revision).toBe(baselineRevisionA);
  expect(winnerWrite.title).toBe(winnerTitle);

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

  // Keep a disjoint local draft in Tab B, then exercise the exact revision-aware
  // mutation boundary against the genuine HA backend. The ordinary Save-button
  // lifecycle (including client-side validation and rerenders) is covered elsewhere;
  // this regression is specifically about rejecting a stale optimistic-concurrency
  // writer without coupling that proof to an unrelated validation/render race.
  await titleB.fill(staleDraftTitle);
  await expect(panelB.getByText("Unsaved changes", {exact: true})).toBeVisible();
  const staleAttempt = await panelB.evaluate(async (element) => {
    try {
      await element._call("configuration", "update", {
        config: structuredClone(element._draft),
        title: element._draftTitle,
        revision: element._configData?.revision,
      });
      return {rejected: false, message: ""};
    } catch (err) {
      return {rejected: true, message: err?.message || String(err)};
    }
  });
  expect(staleAttempt.rejected).toBe(true);
  const staleWrite = await lastConfigurationUpdate(pageB);
  expect(staleWrite).not.toBeNull();
  expect(staleWrite.revision).toBe(baselineRevisionB);
  expect(staleWrite.title).toBe(staleDraftTitle);
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
