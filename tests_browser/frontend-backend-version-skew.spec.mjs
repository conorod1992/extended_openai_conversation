import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.VERSION_SKEW_BACKEND_URL;
const controlUrl = process.env.VERSION_SKEW_CONTROL_URL;
const oldFrontendRoot = process.env.VERSION_SKEW_OLD_FRONTEND_ROOT;
const expectedTitle = process.env.VERSION_SKEW_EXPECTED_TITLE || "Version Skew Agent";
const expectedModel = process.env.VERSION_SKEW_EXPECTED_MODEL || "gpt-5.6";
const authoritativeTitle =
  process.env.VERSION_SKEW_AUTHORITATIVE_TITLE || "Candidate authoritative title";
const staleTitle =
  process.env.VERSION_SKEW_STALE_TITLE || "Old tab stale title";

test.skip(
  !backendUrl || !controlUrl || !oldFrontendRoot,
  "requires released frontend assets and the candidate backend bridge",
);

const fixtureUrl = ({client, frontendRoot = null}) => {
  const params = new URLSearchParams({
    route: "assistant/basics",
    backend: backendUrl,
    client,
  });
  if (frontendRoot) params.set("frontend_root", frontendRoot);
  return `/tests_browser/real-ha-fixture.html?${params.toString()}`;
};

async function waitForOldSaveToBlock(request) {
  await expect.poll(async () => {
    const response = await request.get(`${controlUrl}/status`);
    expect(response.ok()).toBeTruthy();
    return (await response.json()).old_save_waiting;
  }).toBe(true);
}

test("old tab cannot overwrite a newer candidate save", async ({context, request}) => {
  const oldPage = await context.newPage();
  const oldErrors = trackPageErrors(oldPage);

  await oldPage.goto(fixtureUrl({client: "old", frontendRoot: oldFrontendRoot}));
  let oldPanel = oldPage.locator("extended-openai-management-panel");
  await expect(oldPanel.locator('[data-config="__title"]')).toHaveValue(expectedTitle);
  await expect(oldPanel.locator('[data-config="chat_model"]')).toHaveValue(expectedModel);

  // This is a genuine released frontend module. It can read the candidate backend,
  // but its 6.8.3 configuration save does not carry a revision.
  await oldPanel.locator('[data-config="__title"]').fill(staleTitle);
  await oldPanel.locator("#save-config").click();
  await waitForOldSaveToBlock(request);

  // Tab A is still alive on the old JavaScript while Tab B loads the candidate UI.
  const newPage = await context.newPage();
  const newErrors = trackPageErrors(newPage);
  await newPage.goto(fixtureUrl({client: "new"}));
  let newPanel = newPage.locator("extended-openai-management-panel");
  await expect(newPanel.locator('[data-config="__title"]')).toHaveValue(expectedTitle);

  await newPanel.locator('[data-config="__title"]').fill(authoritativeTitle);
  await newPanel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => newPanel.evaluate((host) => host._configDirty)).toBe(false);
  await expect(newPanel.locator('[data-config="__title"]')).toHaveValue(authoritativeTitle);

  // Only now allow Tab A's older mutation to reach the candidate backend. It must
  // fail safely rather than clobbering Tab B's newer revision.
  const released = await request.post(`${controlUrl}/release`);
  expect(released.ok()).toBeTruthy();

  await expect(oldPanel.locator("#toast")).toContainText("Unable to save configuration");
  await expect(oldPanel.locator("#toast")).toContainText("revision");

  // The candidate tab still observes its authoritative state after the delayed old
  // response completes.
  // The harness replaces the URL with the HA route; reload through its entry URL
  // so the backend bridge and client identity remain available after navigation.
  await newPage.goto(fixtureUrl({client: "new"}));
  newPanel = newPage.locator("extended-openai-management-panel");
  await expect(newPanel.locator('[data-config="__title"]')).toHaveValue(authoritativeTitle);
  await expect(newPanel.locator('[data-config="chat_model"]')).toHaveValue(expectedModel);

  // Reloading Tab A onto the candidate frontend converges to the same backend state.
  await oldPage.goto(fixtureUrl({client: "old-reloaded"}));
  oldPanel = oldPage.locator("extended-openai-management-panel");
  await expect(oldPanel.locator('[data-config="__title"]')).toHaveValue(authoritativeTitle);
  await expect(oldPanel.locator('[data-config="chat_model"]')).toHaveValue(expectedModel);

  const oldCalls = await oldPage.evaluate(() => window.browserHarness.calls);
  const newCalls = await newPage.evaluate(() => window.browserHarness.calls);
  expect(oldCalls.some((call) => call.section === "configuration" && call.action === "get")).toBeTruthy();
  expect(newCalls.some((call) => call.section === "configuration" && call.action === "get")).toBeTruthy();

  await expectHarnessClean(newPage, newErrors);
  // The old tab is expected to show one handled save rejection. It must not produce
  // unhandled page errors or rejections.
  const oldHarness = await oldPage.evaluate(() => ({
    errors: window.browserHarness.windowErrors,
    rejections: window.browserHarness.rejections,
  }));
  expect(oldErrors).toHaveLength(0);
  expect(oldHarness).toEqual({errors: [], rejections: []});
  await context.close();
});
