import {expect, test} from "@playwright/test";

function trackPageErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

async function expectHarnessClean(page, pageErrors) {
  const harnessErrors = await page.evaluate(() => ({
    errors: window.browserHarness?.windowErrors || [],
    rejections: window.browserHarness?.rejections || [],
  }));
  expect(pageErrors).toEqual([]);
  expect(harnessErrors).toEqual({errors: [], rejections: []});
}

test("renders the shipped Guide and responds to real browser interactions", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=guide");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Guide", exact: true})).toBeVisible();
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await expect(panel.locator('.guide-quick-card[data-guide-topic="continuity"]')).toBeVisible();

  await panel.locator('.guide-quick-card[data-guide-topic="continuity"]').click();
  await expect(panel.locator("#guide-continuity")).toHaveJSProperty("open", true);

  const search = panel.locator("#guide-search");
  await search.fill("Broadcast");
  await expect(panel.getByText("Broadcast spoken messages around the home", {exact: false}).first()).toBeVisible();
  await expect(panel.getByText("No Guide topics match your search.", {exact: true})).toHaveCount(0);

  await expectHarnessClean(page, pageErrors);
});

test("non-admin browser navigation exposes only the permitted Capabilities section", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=guide&admin=0");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Guide", exact: true})).toBeVisible();
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveCount(0);
  await expect(panel.locator('.top-nav button[data-page="capabilities"]')).toBeVisible();

  await panel.locator('.top-nav button[data-page="capabilities"]').click();
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/guest-mode$/);
  await expect.poll(async () => page.evaluate(() => window.browserHarness.calls.some(
    (call) => call.section === "guest_mode" && call.action === "get",
  ))).toBe(true);

  await expectHarnessClean(page, pageErrors);
});

test("keeps the Overview usable when one websocket-backed section fails", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=overview&partial=1");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Knowledge could not be loaded. Other overview information is still available.", {exact: true})).toBeVisible();
  await expect(panel.getByText("1,234 tokens today", {exact: false})).toBeVisible();
  await expect(panel.getByText("5,678 this month", {exact: false})).toBeVisible();

  const requestedSections = await page.evaluate(() => window.browserHarness.calls
    .filter((call) => call.section)
    .map((call) => `${call.section}/${call.action}`));
  for (const request of ["usage/summary", "conversations/settings", "memories/list", "knowledge/list"]) {
    expect(requestedSections).toContain(request);
  }

  await expectHarnessClean(page, pageErrors);
});
