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

function isConfigurationUpdate(response) {
  if (response.url() !== backendUrl || response.request().method() !== "POST") return false;
  try {
    const message = response.request().postDataJSON();
    return message?.section === "configuration" && message?.action === "update";
  } catch {
    return false;
  }
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
  await expect(titleB).toHaveValue(baselineTitle);

  // Both tabs loaded the same backend revision. Tab A wins the first write and
  // receives the new revision returned by the genuine HA management API.
  await titleA.fill("Browser multi-tab winner");
  await saveConfig(panelA);
  await expect(panelA.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(titleA).toHaveValue("Browser multi-tab winner");

  // Tab B still holds the older revision. Its disjoint local draft must be
  // rejected rather than replacing Tab A's newer full configuration snapshot.
  // Wait for the actual configuration/update bridge response rather than a DOM
  // node: the panel may re-render while handling the failed request, replacing
  // the save button and transient toast. The completed non-2xx update plus the
  // durable draft/backend assertions below prove the stale write was rejected.
  await titleB.fill("Browser multi-tab stale draft");
  await expect(panelB.getByText("Unsaved changes", {exact: true})).toBeVisible();
  const staleUpdateResponse = pageB.waitForResponse(isConfigurationUpdate);
  await panelB.getByRole("button", {name: "Save configuration", exact: true}).click();
  const staleResponse = await staleUpdateResponse;
  expect(staleResponse.ok()).toBe(false);
  await expect(titleB).toHaveValue("Browser multi-tab stale draft");
  await expect(panelB.getByText("Unsaved changes", {exact: true})).toBeVisible();

  // A third independently loaded page proves the backend retained the winner and
  // did not partially apply any stale fields from Tab B.
  const pageC = await context.newPage();
  const pageErrorsC = trackPageErrors(pageC);
  let panelC = await openConfig(pageC);
  await expect(panelC.locator('[data-config="__title"]')).toHaveValue("Browser multi-tab winner");

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
