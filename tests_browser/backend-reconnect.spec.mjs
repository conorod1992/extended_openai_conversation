import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("mounted management panel recovers after HA backend disconnect and reconnect", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  const marker = await page.evaluate(() => {
    const value = `mounted-${Math.random()}`;
    window.browserHarness.panel.__reconnectMarker = value;
    window.browserHarness.backendOnline = true;
    window.browserHarness.backendDisconnectAttempts = 0;
    const originalCallWS = window.browserHarness.hass.callWS.bind(window.browserHarness.hass);
    window.browserHarness.hass.callWS = async (message) => {
      if (!window.browserHarness.backendOnline) {
        window.browserHarness.backendDisconnectAttempts += 1;
        throw new Error("Home Assistant backend disconnected");
      }
      return originalCallWS(message);
    };
    return value;
  });

  await page.evaluate(() => { window.browserHarness.backendOnline = false; });
  await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();

  await expect(page).toHaveURL(/\/extended-openai\/usage-maintenance\/usage$/);
  await expect.poll(async () => page.evaluate(() => window.browserHarness.backendDisconnectAttempts)).toBeGreaterThan(0);

  // A disconnected backend must not tear down the mounted panel. The requested
  // section still renders with explicit unavailable states for backend-backed
  // data, while locally renderable usage content remains usable.
  await expect(panel.getByRole("heading", {name: "Usage period", exact: true})).toBeVisible();
  await expect(panel.getByText("Usage summary unavailable", {exact: true})).toBeVisible();
  await expect(panel.getByText(/Home Assistant backend disconnected/).first()).toBeVisible();
  const disconnectedState = await page.evaluate(() => ({
    marker: window.browserHarness.panel.__reconnectMarker,
    connected: window.browserHarness.panel.isConnected,
    samePanel: window.browserHarness.panel === document.querySelector("extended-openai-management-panel"),
  }));
  expect(disconnectedState).toEqual({marker, connected: true, samePanel: true});

  await page.evaluate(() => { window.browserHarness.backendOnline = true; });
  await panel.locator('.top-nav button[data-page="capabilities"]').click();

  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/home-assistant$/);
  await expect(panel.getByRole("heading", {name: "Home Assistant access", exact: true})).toBeVisible();
  await expect(panel.getByRole("alert")).toHaveCount(0);

  const recoveredState = await page.evaluate(() => ({
    marker: window.browserHarness.panel.__reconnectMarker,
    connected: window.browserHarness.panel.isConnected,
    samePanel: window.browserHarness.panel === document.querySelector("extended-openai-management-panel"),
  }));
  expect(recoveredState).toEqual({marker, connected: true, samePanel: true});

  await expectHarnessClean(page, pageErrors);
});