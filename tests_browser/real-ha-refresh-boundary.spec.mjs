import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");

const realFixtureUrl = (route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

const MEMORY = "Real HA refresh-boundary memory";

test("browser history retains the visible Assistant draft against genuine HA", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(realFixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible();
  const original = await title.inputValue();
  await title.fill("Unsaved history draft");
  await panel.locator("#local-section").selectOption("model-responses", {force: true});
  await expect(panel.locator("#local-section")).toHaveValue("model-responses");
  await page.goBack();
  await expect(page).toHaveURL(/\/assistant\/basics$/);
  await expect(title).toHaveValue("Unsaved history draft");
  await page.goForward();
  await expect(page).toHaveURL(/\/assistant\/model-responses$/);
  await expect(panel.locator("#local-section")).toHaveValue("model-responses");
  await page.goBack();
  await expect(title).toHaveValue("Unsaved history draft");
  await title.fill(original);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expectHarnessClean(page, errors);
});
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

  // A fresh full-document navigation destroys the unresolved frontend promise.
  // Re-enter through the dedicated fixture URL rather than reloading the synthetic
  // production-style path that the static test server does not actually own.
  await page.goto(realFixtureUrl("data-memory/memories"), {waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  const matchingCards = panel.locator(".list-card").filter({hasText: MEMORY});
  await expect(matchingCards).toHaveCount(1);
  await expect(matchingCards.first()).toContainText("refresh-boundary");

  // Start one more fresh document to prove the persisted backend state is stable,
  // not merely a transient render artifact from the first post-boundary load.
  await page.goto(realFixtureUrl("data-memory/memories"), {waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  const persisted = panel.locator(".list-card").filter({hasText: MEMORY});
  await expect(persisted).toHaveCount(1);

  // Leave the shared genuine-HA browser fixture clean for any following journeys.
  await persisted.locator(".delete-memory").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".list-card").filter({hasText: MEMORY})).toHaveCount(0);

  await page.goto(realFixtureUrl("data-memory/memories"), {waitUntil: "domcontentloaded"});
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".list-card").filter({hasText: MEMORY})).toHaveCount(0);
  await expectHarnessClean(page, pageErrors);
});
