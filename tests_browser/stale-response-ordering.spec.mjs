import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("older backend response cannot overwrite a newer mounted-panel route", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  const originalPanel = await panel.evaluate((element) => {
    window.__staleResponsePanel = element;
    return true;
  });
  expect(originalPanel).toBe(true);

  await page.evaluate(() => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let releaseKnowledge;
    let started = 0;
    const knowledgeGate = new Promise((resolve) => {
      releaseKnowledge = resolve;
    });

    window.__staleResponseControl = {
      get started() { return started; },
      release() { releaseKnowledge(); },
    };

    hass.callWS = async (request) => {
      if (request.section === "knowledge" && request.action === "list") {
        started += 1;
        await knowledgeGate;
      }
      return original(request);
    };
  });

  await page.evaluate(() => {
    history.pushState({}, "", "/extended-openai/data-memory/knowledge");
    window.browserHarness.panel.route = {};
  });

  await expect.poll(() => page.evaluate(() => window.__staleResponseControl.started)).toBe(1);

  // Move to a newer route while the Knowledge response is still held. The same
  // custom element remains mounted, so only generation/stale-response protection
  // can stop the older result from repainting the panel after it finally arrives.
  await page.evaluate(() => {
    history.pushState({}, "", "/extended-openai/data-memory/memories");
    window.browserHarness.panel.route = {};
  });
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(page).toHaveURL(/\/extended-openai\/data-memory\/memories$/);

  await page.evaluate(() => window.__staleResponseControl.release());
  await page.waitForTimeout(50);

  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toHaveCount(0);
  await expect(page).toHaveURL(/\/extended-openai\/data-memory\/memories$/);
  expect(await page.evaluate(() => window.browserHarness.panel === window.__staleResponsePanel)).toBe(true);

  await expectHarnessClean(page, pageErrors);
});
