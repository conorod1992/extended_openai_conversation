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

test("replays hass and route when Home Assistant creates the panel before definition", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=guide&predefine=1");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Guide", exact: true})).toBeVisible();
  await expect(panel.locator("#agent")).toHaveValue("agent-1");

  const lifecycle = await page.evaluate(() => ({
    defined: customElements.get("extended-openai-management-panel") != null,
    ownHass: Object.prototype.hasOwnProperty.call(window.browserHarness.panel, "hass"),
    ownRoute: Object.prototype.hasOwnProperty.call(window.browserHarness.panel, "route"),
    agentLoads: window.browserHarness.calls.filter(
      (call) => call.action === "agents" && !call.section,
    ).length,
  }));
  expect(lifecycle).toEqual({defined: true, ownHass: false, ownRoute: false, agentLoads: 1});

  await expectHarnessClean(page, pageErrors);
});

test("loads, edits, and saves administrator configuration through the shipped editor", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=assistant/basics");

  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toHaveValue("Jarvis");
  await expect(panel.locator('[data-config="chat_model"]')).toHaveValue("gpt-5-mini");

  await title.fill("Kitchen Jarvis");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();

  await expect.poll(async () => page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "configuration" && call.action === "save",
  ).length)).toBe(1);

  const saveRequest = await page.evaluate(() => window.browserHarness.calls.find(
    (call) => call.section === "configuration" && call.action === "save",
  ));
  expect(saveRequest.title).toBe("Kitchen Jarvis");
  expect(saveRequest.config.chat_model).toBe("gpt-5-mini");
  expect(saveRequest.config.max_tokens).toBe(1200);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  await expect(panel.locator("#agent option:checked")).toHaveText("Kitchen Jarvis");

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

test("direct non-admin URLs cannot load administrator configuration", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=assistant/basics&admin=0");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Administrator permission is required for this section.", {exact: true})).toBeVisible();
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveCount(0);

  const configurationRequests = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "configuration",
  ));
  expect(configurationRequests).toEqual([]);

  await expectHarnessClean(page, pageErrors);
});

test("keeps the Overview usable when its summary reports a partial backend failure", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto("/tests_browser/fixture.html?route=overview&partial=1");

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Knowledge could not be loaded. Other overview information is still available.", {exact: true})).toBeVisible();
  await expect(panel.getByText("1,234 tokens today", {exact: false})).toBeVisible();
  await expect(panel.getByText("5,678 this month", {exact: false})).toBeVisible();

  const overviewRequests = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "overview" && call.action === "summary",
  ));
  expect(overviewRequests).toHaveLength(1);

  await expectHarnessClean(page, pageErrors);
});
