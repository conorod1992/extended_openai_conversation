import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Usage renders aggregate detail and navigates into retention settings", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/usage"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Input footprint", exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Usage period", exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Tokens by recorded day", exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Recent runs", exact: true})).toBeVisible();
  await expect(panel.getByText("9,999", {exact: true})).toBeVisible();
  await expect(panel.getByText("No retained recent runs.", {exact: true})).toBeVisible();

  const usageCalls = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "usage",
  ).map((call) => call.action));
  expect(usageCalls).toEqual(expect.arrayContaining(["summary", "daily", "runs", "retention"]));

  await panel.getByRole("button", {name: "Configure retention", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/usage-maintenance\/retention$/);
  await expect(panel.locator('[data-config="usage_request_retention_days"]')).toBeVisible();
  await expect(panel.locator('[data-config="usage_run_retention_days"]')).toBeVisible();

  await expectHarnessClean(page, pageErrors);
});

test("Knowledge Library opens a real editor and protects an unsaved draft", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Knowledge Library", exact: true})).toBeVisible();
  await expect(panel.getByText("No Knowledge sources yet. Add one to make reference information available on demand.", {exact: true})).toBeVisible();

  await panel.locator("#add-source").click();
  const dialog = panel.locator("#knowledge-dialog");
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(panel.locator("#knowledge-dialog-title")).toHaveText("Add Knowledge source");

  await panel.locator("#knowledge-title").fill("Browser knowledge draft");
  await panel.locator("#knowledge-description").fill("Draft used to exercise the Knowledge editor.");
  await panel.locator("#knowledge-content").fill("The browser fixture has a deliberately unsaved source.");
  await expect(panel.locator("#knowledge-counter")).toContainText("characters");

  await dialog.getByRole("button", {name: "Cancel", exact: true}).click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#confirm-title")).toHaveText("Discard unsaved changes?");
  await panel.locator("#confirm-cancel").click();
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(panel.locator("#knowledge-title")).toHaveValue("Browser knowledge draft");

  await dialog.getByRole("button", {name: "Cancel", exact: true}).click();
  await acceptConfirmation(panel);
  await expect(dialog).toHaveJSProperty("open", false);

  const knowledgeCalls = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "knowledge",
  ).map((call) => call.action));
  expect(knowledgeCalls).toEqual(["list"]);

  await expectHarnessClean(page, pageErrors);
});

test("Conversation history loads scoped archive state through the management UI", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/conversations"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#scope")).toHaveValue("user:test-user");
  await expect(panel.getByText("Conversation archive enabled", {exact: true})).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Retained conversations", exact: true})).toBeVisible();
  await expect(panel.getByText("No retained conversations in this scope.", {exact: true})).toBeVisible();
  await expect(panel.getByText("Continuity is recent context used for follow-ups. The archive is retained history; configure its behavior below.", {exact: true})).toBeVisible();

  const calls = await page.evaluate(() => window.browserHarness.calls.map(
    (call) => `${call.section || "root"}/${call.action}`,
  ));
  expect(calls).toEqual(expect.arrayContaining([
    "scopes/catalog",
    "conversations/list",
    "conversations/settings",
    "conversations/active",
    "configuration/get",
  ]));

  await panel.locator("#archive-query").fill("kitchen project");
  await expect(panel.locator("#archive-query")).toHaveValue("kitchen project");

  await expectHarnessClean(page, pageErrors);
});
