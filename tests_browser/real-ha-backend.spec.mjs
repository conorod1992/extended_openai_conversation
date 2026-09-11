import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
if (!backendUrl) throw new Error("REAL_HA_BACKEND_URL is required for genuine HA browser acceptance");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

test("real browser saves General Settings through the genuine HA backend", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("assistant/basics"));

  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible();
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();

  await title.fill("Browser Real HA Saved");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await page.goto(realFixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Browser Real HA Saved");
  await expect(panel.locator("#agent option:checked")).toHaveText("Browser Real HA Saved");

  const actions = await page.evaluate(() => window.browserHarness.calls
    .filter((call) => call.section === "configuration")
    .map((call) => call.action));
  expect(actions).toContain("get");
  await expectHarnessClean(page, pageErrors);
});

test("real browser creates, edits, reloads, and deletes a Memory through HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("data-memory/memories"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill("Real HA browser memory");
  await panel.locator("#memory-category").fill("browser-acceptance");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Real HA browser memory", {exact: true})).toBeVisible();

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  let card = panel.locator(".list-card").filter({hasText: "Real HA browser memory"});
  await expect(card).toBeVisible();
  await card.locator(".memory-edit-button").click();
  await panel.locator("#memory-content").fill("Real HA browser memory edited");
  await panel.locator("#memory-category").fill("browser-acceptance-edited");
  await panel.locator("#memory-save").click();

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".list-card").filter({hasText: "Real HA browser memory edited"});
  await expect(card).toContainText("browser-acceptance-edited");
  await card.locator(".delete-memory").click();
  await acceptConfirmation(panel);
  await expect(panel.getByText("Real HA browser memory edited", {exact: true})).toHaveCount(0);

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Real HA browser memory edited", {exact: true})).toHaveCount(0);
  await expectHarnessClean(page, pageErrors);
});

test("real browser creates, edits, reloads, and deletes a Request Rule through HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/request-rules"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("Real HA browser rule");
  await panel.locator("#rule-phrases").fill("real browser route");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("conversation");
  await panel.locator("#rule-save").click();
  await expect(panel.getByRole("heading", {name: "Real HA browser rule", exact: true})).toBeVisible();

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  let card = panel.locator(".request-rule-card").filter({hasText: "Real HA browser rule"});
  await expect(card).toBeVisible();
  await card.locator(".rule-edit").click();
  await panel.locator("#rule-name").fill("Real HA browser rule edited");
  await panel.locator("#rule-model").fill("gpt-5-nano");
  await panel.locator("#rule-save").click();

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".request-rule-card").filter({hasText: "Real HA browser rule edited"});
  await expect(card).toContainText("gpt-5-nano");
  await card.locator(".rule-delete").click();
  await acceptConfirmation(panel);
  await expect(panel.getByRole("heading", {name: "Real HA browser rule edited", exact: true})).toHaveCount(0);

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Real HA browser rule edited", exact: true})).toHaveCount(0);
  await expectHarnessClean(page, pageErrors);
});
