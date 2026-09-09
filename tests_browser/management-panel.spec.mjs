import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("renders the shipped Guide and responds to real browser interactions", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("guide"));

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
  await page.goto(fixtureUrl("guide", "&predefine=1"));

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

test("general configuration survives a fresh panel load and a rejected save can be retried", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&fail_config_once=1"));

  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toHaveValue("Jarvis");
  await expect(panel.locator('[data-config="chat_model"]')).toHaveValue("gpt-5-mini");

  await title.fill("Kitchen Jarvis");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();

  await expect.poll(async () => page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "configuration" && call.action === "save",
  ).length)).toBe(1);
  await expect(panel.getByText("Unable to save configuration: Fixture rejected configuration save once", {exact: true})).toBeVisible();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Jarvis");

  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Kitchen Jarvis");

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Kitchen Jarvis");
  await expect(panel.locator("#agent option:checked")).toHaveText("Kitchen Jarvis");

  const getRequest = await page.evaluate(() => window.browserHarness.calls.find(
    (call) => call.section === "configuration" && call.action === "get",
  ));
  expect(getRequest).toBeTruthy();
  await expectHarnessClean(page, pageErrors);
});

test("non-admin browser navigation exposes only the permitted Capabilities section", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("guide", "&admin=0"));

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

test("mobile layout exposes working responsive navigation", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(fixtureUrl("guide"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Guide", exact: true})).toBeVisible();
  await expect(panel.locator(".top-nav")).toBeHidden();
  await expect(panel.locator("#top-section-mobile")).toBeVisible();

  await panel.locator("#top-section-mobile").selectOption("capabilities");
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/home-assistant$/);
  await expect(panel.getByRole("heading", {name: "Home Assistant access", exact: true})).toBeVisible();

  await expectHarnessClean(page, pageErrors);
});

test("direct non-admin URLs cannot load administrator configuration", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&admin=0"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Administrator permission is required for this section.", {exact: true})).toBeVisible();
  await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveCount(0);

  const configurationRequests = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "configuration",
  ));
  expect(configurationRequests).toEqual([]);

  await expectHarnessClean(page, pageErrors);
});

test("keeps the Overview usable when one backend section rejects", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview", "&partial=1"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Jarvis", exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Capabilities", exact: true})).toBeVisible();
  await expect(panel.getByText("1 functions · 1 groups", {exact: true})).toBeVisible();

  const overviewRequests = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => ["usage", "conversations", "memories", "knowledge"].includes(call.section),
  ));
  expect(overviewRequests.some((call) => call.section === "usage" && call.action === "summary")).toBe(true);
  expect(overviewRequests.some((call) => call.section === "knowledge" && call.action === "list")).toBe(true);

  await expectHarnessClean(page, pageErrors);
});
