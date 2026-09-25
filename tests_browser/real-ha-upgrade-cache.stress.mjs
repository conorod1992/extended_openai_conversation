import {expect, test} from "@playwright/test";
import {existsSync} from "node:fs";
import path from "node:path";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const oldRoot = process.env.EOAI_OLD_FRONTEND_DIR;
test.skip(!backendUrl || !oldRoot, "requires a published frontend and genuine HA backend");
const fixture = route => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;
const panel = page => page.locator("extended-openai-management-panel");
const title = page => panel(page).locator('[data-config="__title"]');

async function servePublishedFrontend(page, onlyChunk = false) {
  let served = 0;
  const pattern = "**/custom_components/extended_openai_conversation_responses/frontend/**";
  await page.route(pattern, async route => {
    const relative = new URL(route.request().url()).pathname.split("/frontend/")[1];
    if (!relative || (onlyChunk && relative !== "agent-config-editor.js")) return route.continue();
    const file = path.join(oldRoot, relative);
    if (!existsSync(file)) return route.continue();
    served++;
    return route.fulfill({path: file, contentType: "text/javascript"});
  });
  return {pattern, count: () => served};
}

test("old frontend shell kept open against new HA backend converges after hard refresh", async ({page, context}) => {
  const old = await servePublishedFrontend(page);
  await page.goto(fixture("assistant/basics"));
  await expect(panel(page)).toHaveCount(1);
  await expect(title(page)).toBeVisible();
  expect(old.count()).toBeGreaterThan(0);
  const before = await title(page).inputValue();

  // The old tab remains mounted while a new-asset tab writes to the same genuine
  // HA configuration. A full refresh must replace its stale module graph.
  await page.unroute(old.pattern);
  const fresh = await context.newPage();
  const updated = `Upgrade browser ${process.env.STRESS_SEED || "0"}`;
  try {
    await fresh.goto(fixture("assistant/basics"));
    await expect(title(fresh)).toHaveValue(before);
    await title(fresh).fill(updated);
    await panel(fresh).getByRole("button", {name: "Save changes", exact: true}).click();
    await expect(panel(fresh).getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  } finally { await fresh.close(); }
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toHaveValue(updated);
});

test("new frontend with a stale published lazy chunk recovers on a fresh document", async ({page}) => {
  const old = await servePublishedFrontend(page, true);
  await page.goto(fixture("assistant/basics"));
  await expect(panel(page)).toHaveCount(1);
  await expect.poll(old.count).toBeGreaterThan(0);
  await page.unroute(old.pattern);
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
});

test("lazy chunk 404 reports a section error and refresh loads current assets", async ({page}) => {
  let missing = 0;
  const pattern = "**/management-guest-feature.js";
  await page.route(pattern, route => { missing++; return route.fulfill({status: 404, body: ""}); });
  await page.goto(fixture("capabilities/guest-mode"));
  await expect.poll(() => missing).toBeGreaterThan(0);
  await expect(panel(page).locator("main [role=alert]")).toContainText("Unable to load this frontend section");
  await page.unroute(pattern);
  await page.goto(fixture("capabilities/guest-mode"));
  await expect(panel(page).locator("#guest-now")).toBeVisible();
});

test("malformed browser-local cache clears through normal reload", async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem("extended-openai-debug-agent", "{stale:unknown-agent}");
    sessionStorage.setItem("eocRealHaCalls", "not-json");
  });
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
  await page.goto(fixture("assistant/basics"));
  await expect(title(page)).toBeVisible();
});
