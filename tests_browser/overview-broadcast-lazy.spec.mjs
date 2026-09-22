import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Overview is usable while Broadcast implementation is still loading", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const requested = new Promise((resolve) => {
    page.route("**/overview-broadcast.js", async (route) => {
      await new Promise((unblock) => {
        release = unblock;
        resolve();
      });
      await route.continue();
    });
  });

  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await requested;

  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await expect(panel.locator(".setup-health")).toBeVisible();
  await expect(panel.locator("#broadcast-card")).toContainText("Loading Broadcast…");
  await expect(panel.locator("#broadcast-enabled")).toHaveCount(0);

  const marksBeforeBroadcast = await page.evaluate(() =>
    new Set(performance.getEntriesByType("mark").map((entry) => entry.name))
  );
  expect([...marksBeforeBroadcast]).toContain("extended-openai:cold:overview-content-present");

  release();
  await expect(panel.locator("#broadcast-card")).not.toContainText("Loading Broadcast…");
  await expect(panel.locator("#broadcast-card")).toContainText("Broadcast");
  await expectHarnessClean(page, errors);
});

test("leaving Overview while Broadcast loads prevents stale hydration", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const requested = new Promise((resolve) => {
    page.route("**/overview-broadcast.js", async (route) => {
      await new Promise((unblock) => {
        release = unblock;
        resolve();
      });
      await route.continue();
    });
  });

  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await requested;
  await panel.evaluate((host) => host._navigate("guide"));
  await expect(panel.locator(".guide-search")).toBeVisible();

  release();
  await page.waitForTimeout(0);
  await expect(panel.locator(".guide-search")).toBeVisible();
  await expect(panel.locator("#broadcast-card")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});
