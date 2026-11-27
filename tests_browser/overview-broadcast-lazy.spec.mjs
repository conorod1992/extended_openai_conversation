test("Overview snapshot is usable while the full implementation is still loading", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const requested = new Promise((resolve) => {
    page.route("**/overview-page-impl*.js", async (route) => {
      await new Promise((unblock) => {
        release = unblock;
        resolve();
      });
      await route.continue();
    });
  });

  await page.goto(fixtureUrl("overview"), {waitUntil:"domcontentloaded"});
  const panel = page.locator("extended-openai-management-panel");
  await requested;

  await expect(panel.locator(".eoc-overview-snapshot")).toBeVisible();
  await expect(panel.locator(".dashboard-card")).toHaveCount(8);
  const assistantCard = panel.locator(".dashboard-card").first();
  await expect(assistantCard.locator("h3")).not.toBeEmpty();
  await expect(assistantCard.locator("strong")).toContainText("·");
  await expect(assistantCard.locator("button[data-page='assistant'][data-subsection='basics']")).toBeVisible();
  await expect(panel.locator(".setup-health")).toHaveCount(0);
  expect(await page.evaluate(() =>
    performance.getEntriesByType("mark")
      .some((entry) => entry.name === "extended-openai:cold:overview-content-present")
  )).toBe(true);

  release();
  await expect(panel.locator(".eoc-overview-snapshot")).toHaveCount(0);
  await expect(panel.locator(".setup-health")).toBeVisible();
  await expectHarnessClean(page, errors);
});

import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Overview is usable while Broadcast implementation is still loading", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const requested = new Promise((resolve) => {
    page.route("**/overview-broadcast*.js", async (route) => {
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

  expect(await page.evaluate(() =>
    performance.getEntriesByType("mark")
      .some((entry) => entry.name === "extended-openai:cold:overview-content-present")
  )).toBe(true);

  release();
  await expect(panel.locator("#broadcast-card")).not.toContainText("Loading Broadcast…");
  await expect(panel.locator("#broadcast-card")).toContainText("Broadcast");
  await expectHarnessClean(page, errors);
});

test("leaving Overview while Broadcast loads prevents stale hydration", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const requested = new Promise((resolve) => {
    page.route("**/overview-broadcast*.js", async (route) => {
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

test("reloading Overview restarts Broadcast polling without replacing the composer", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    window.broadcastReloadReads = 0;
    host.hass.callWS = async request => {
      if (!request.type.endsWith("/broadcast") || request.action !== "snapshot") return call(request);
      const delivered = window.broadcastReloading && ++window.broadcastReloadReads > 1;
      const item = {id:"pending-delivery", message:"Waiting announcement", created_at:new Date().toISOString(),
        deliveries:{"assist_satellite.kitchen":{status:delivered ? "delivered" : "waiting_idle"}}};
      return {enabled:true, can_manage:true, catalog:{satellites:[{id:"assist_satellite.kitchen", name:"Kitchen"}]},
        pending:delivered ? [] : [item], history:delivered ? [item] : []};
    };
    return host._navigate("overview");
  });
  const composer = panel.locator("#broadcast-message");
  await composer.fill("An unsent draft");
  const original = await composer.elementHandle();
  const history = panel.locator(".broadcast-history");
  await expect(history).toContainText("Waiting for idle");
  await panel.evaluate(host => {
    host._eocSectionCacheTimes.set(host._sectionCacheKey("overview"), Date.now() - 60000);
    window.broadcastReloading = true;
  });
  await panel.locator('.top-nav button[data-page="overview"]').click();
  await composer.evaluate(node => { node.focus(); node.setSelectionRange(5, 5); });
  await expect(history).toContainText("Delivered");
  expect(await page.evaluate(() => window.broadcastReloadReads)).toBeGreaterThanOrEqual(2);
  expect(await original.evaluate(node => node.isConnected)).toBe(true);
  await expect(composer).toHaveValue("An unsent draft");
  await expect(composer).toBeFocused();
  expect(await composer.evaluate(node => node.selectionStart)).toBe(5);
  await expectHarnessClean(page, errors);
});

test("revisiting cached Overview retries a failed initial Broadcast snapshot", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    window.broadcastRecoveryReads = 0;
    host.hass.callWS = async request => {
      if (request.type.endsWith("/broadcast") && request.action === "snapshot" && ++window.broadcastRecoveryReads === 1) {
        throw new Error("Snapshot temporarily unavailable");
      }
      return call(request);
    };
    return host._navigate("overview");
  });
  const card = panel.locator("#broadcast-card");
  await expect(card).toContainText("Snapshot temporarily unavailable");
  const original = await card.elementHandle();
  await panel.locator('.top-nav button[data-page="overview"]').click();
  await expect(panel.locator("#broadcast-enabled")).toBeVisible();
  expect(await page.evaluate(() => window.broadcastRecoveryReads)).toBe(2);
  expect(await original.evaluate(node => node.isConnected)).toBe(true);
  await expectHarnessClean(page, errors);
});
