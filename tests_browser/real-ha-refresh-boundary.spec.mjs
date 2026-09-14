import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");

const realFixtureUrl = (route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

const MEMORY = "Real HA refresh-boundary memory";

test("hard refresh after committed Memory mutation converges without replay", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("data-memory/memories"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  // Let the genuine backend request run to completion, but deliberately keep the
  // promise returned to the management panel unresolved. This creates the exact
  // boundary we care about: HA has committed the mutation while the old page still
  // believes the save is in flight.
  await page.evaluate((memoryText) => {
    const originalFetch = window.fetch.bind(window);
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    window.__refreshBoundary = {committed: false, release};

    window.fetch = async (...args) => {
      const [, init] = args;
      const body = typeof init?.body === "string" ? init.body : "";
      const response = await originalFetch(...args);
      if (body.includes(memoryText)) {
        window.__refreshBoundary.committed = true;
        await gate;
      }
      return response;
    };
  }, MEMORY);

  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill(MEMORY);
  await panel.locator("#memory-category").fill("refresh-boundary");
  await panel.locator("#memory-save").click();

  await expect.poll(() => page.evaluate(() => window.__refreshBoundary?.committed)).toBe(true);
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);

  // A hard reload destroys the unresolved frontend promise. The integration must
  // not replay the already-committed mutation when the fresh panel mounts.
  await page.reload({waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  const matchingCards = panel.locator(".list-card").filter({hasText: MEMORY});
  await expect(matchingCards).toHaveCount(1);
  await expect(matchingCards.first()).toContainText("refresh-boundary");

  // Reload once more to prove the persisted backend state is stable, not merely a
  // transient render artifact from the first post-refresh load.
  await page.reload({waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  const persisted = panel.locator(".list-card").filter({hasText: MEMORY});
  await expect(persisted).toHaveCount(1);

  // Leave the shared genuine-HA browser fixture clean for any following journeys.
  await persisted.locator(".delete-memory").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".list-card").filter({hasText: MEMORY})).toHaveCount(0);

  await page.reload({waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".list-card").filter({hasText: MEMORY})).toHaveCount(0);
  await expectHarnessClean(page, pageErrors);
});
