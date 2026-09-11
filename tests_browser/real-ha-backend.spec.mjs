import {expect, test} from "@playwright/test";
import {acceptConfirmation, browserToolYaml, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");
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

test("real browser manages a Function Tool and dependent Group through genuine HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();

  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#tool-yaml").fill(browserToolYaml("Real HA browser tool"));
  await panel.locator("#tool-save").click();
  let tool = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(tool).toContainText("Real HA browser tool");

  await panel.locator("#add-group").click();
  await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#group-name").fill("Real HA browser group");
  await panel.locator("#group-id").fill("real-ha-browser-group");
  await panel.locator("#group-description").fill("Real HA browser group description");
  await panel.locator('#group-functions input[value="browser_tool"]').check();
  await panel.locator("#group-save").click();
  let group = panel.locator('.function-group-card[data-group-id="real-ha-browser-group"]');
  await expect(group).toContainText("Real HA browser group");

  await page.goto(realFixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  tool = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  group = panel.locator('.function-group-card[data-group-id="real-ha-browser-group"]');
  await expect(tool).toContainText("Real HA browser tool");
  await expect(group).toContainText("Real HA browser group description");
  await group.locator("summary").click();
  await expect(group.locator(".tool-card").filter({hasText: "browser_tool"})).toBeVisible();

  await tool.locator(".edit-tool").click();
  await panel.locator("#tool-yaml").fill(browserToolYaml("Real HA browser tool edited"));
  await panel.locator("#tool-save").click();
  await group.locator(".edit-group").click();
  await panel.locator("#group-name").fill("Real HA browser group edited");
  await panel.locator("#group-description").fill("Real HA browser group description edited");
  await panel.locator("#group-save").click();

  await page.goto(realFixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  tool = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  group = panel.locator('.function-group-card[data-group-id="real-ha-browser-group"]');
  await expect(tool).toContainText("Real HA browser tool edited");
  await expect(group).toContainText("Real HA browser group edited");
  await expect(group).toContainText("Real HA browser group description edited");

  await group.locator(".delete-group").click();
  await acceptConfirmation(panel);
  await expect(panel.locator('.function-group-card[data-group-id="real-ha-browser-group"]')).toHaveCount(0);
  await tool.locator(".delete-tool").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toHaveCount(0);

  await page.goto(realFixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('.function-group-card[data-group-id="real-ha-browser-group"]')).toHaveCount(0);
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toHaveCount(0);
  await expectHarnessClean(page, pageErrors);
});

test("real browser full backup restores cross-feature state through genuine HA", async ({page}) => {
  const pageErrors = trackPageErrors(page);

  await page.goto(realFixtureUrl("assistant/basics"));
  let panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Real HA backup source");
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Real HA backup memory");
  await panel.locator("#memory-category").fill("backup-acceptance");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Real HA backup memory", {exact: true})).toBeVisible();

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await panel.locator("#rule-name").fill("Real HA backup rule");
  await panel.locator("#rule-phrases").fill("real ha backup route");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("conversation");
  await panel.locator("#rule-save").click();
  await expect(panel.getByRole("heading", {name: "Real HA backup rule", exact: true})).toBeVisible();

  await page.goto(realFixtureUrl("usage-maintenance/backup-restore"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const download = await downloadPromise;
  const backupPath = await download.path();
  expect(backupPath).toBeTruthy();

  await page.goto(realFixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Real HA backup mutated");
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  let card = panel.locator(".list-card").filter({hasText: "Real HA backup memory"});
  await card.locator(".delete-memory").click();
  await acceptConfirmation(panel);

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".request-rule-card").filter({hasText: "Real HA backup rule"});
  await card.locator(".rule-delete").click();
  await acceptConfirmation(panel);

  await page.goto(realFixtureUrl("usage-maintenance/backup-restore"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#backup-file-transfer").setInputFiles(backupPath);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);

  await page.goto(realFixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Real HA backup source");

  await page.goto(realFixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Real HA backup memory", {exact: true})).toBeVisible();

  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Real HA backup rule", exact: true})).toBeVisible();
  await expectHarnessClean(page, pageErrors);
});
