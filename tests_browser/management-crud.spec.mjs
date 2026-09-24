import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("persistent memories support create, reload, edit, and delete", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill("Browser journey memory");
  await panel.locator("#memory-category").fill("testing");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Browser journey memory", {exact: true})).toBeVisible();

  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  let card = panel.locator(".list-card").filter({hasText: "Browser journey memory"});
  await expect(card).toBeVisible();
  await card.locator(".memory-edit-button").click();
  await panel.locator("#memory-content").fill("Browser journey memory edited");
  await panel.locator("#memory-category").fill("testing-edited");
  await panel.locator("#memory-save").click();

  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".list-card").filter({hasText: "Browser journey memory edited"});
  await expect(card).toContainText("testing-edited");
  await card.locator(".delete-memory").click();
  await acceptConfirmation(panel);
  await expect(panel.getByText("Browser journey memory edited", {exact: true})).toHaveCount(0);

  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Browser journey memory edited", {exact: true})).toHaveCount(0);
  await expect(panel.getByText("Baseline browser fixture memory", {exact: true})).toBeVisible();
  await expectHarnessClean(page, pageErrors);
});

test("Request Rules support create, precedence changes, reload, edit, and delete", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("Browser rule");
  await panel.locator("#rule-phrases").fill("browser route");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("request");
  await panel.locator("#rule-save").click();
  await expect(panel.getByRole("heading", {name: "Browser rule", exact: true})).toBeVisible();

  let card = panel.locator(".request-rule-card").filter({hasText: "Browser rule"});
  await card.locator('.rule-move[data-direction="up"]').click();
  await expect.poll(async () => panel.locator(".request-rule-card h2").allTextContents()).toEqual(["Browser rule", "Baseline rule"]);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect.poll(async () => panel.locator(".request-rule-card h2").allTextContents()).toEqual(["Browser rule", "Baseline rule"]);
  card = panel.locator(".request-rule-card").filter({hasText: "Browser rule"});
  await card.locator(".rule-edit").click();
  await panel.locator("#rule-name").fill("Browser rule edited");
  await panel.locator("#rule-model").fill("gpt-5-nano");
  await panel.locator("#rule-save").click();
  await expect(panel.getByRole("heading", {name: "Browser rule edited", exact: true})).toBeVisible();

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".request-rule-card").filter({hasText: "Browser rule edited"});
  await expect(card).toContainText("gpt-5-nano");
  await card.locator(".rule-delete").click();
  await acceptConfirmation(panel);
  await expect(panel.getByRole("heading", {name: "Browser rule edited", exact: true})).toHaveCount(0);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Browser rule edited", exact: true})).toHaveCount(0);
  await expect(panel.getByRole("heading", {name: "Baseline rule", exact: true})).toBeVisible();
  await expectHarnessClean(page, pageErrors);
});

test("Request Rule condition selector, local continuation, and group survive reload", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");
  await panel.locator(".rule-groups summary").click();
  await panel.locator("#rule-new-group-name").fill("Kitchen");
  await panel.locator("#rule-group-add").click();
  await expect(panel.locator(".rule-group-row")).toHaveCount(1);
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Conditional local");
  await panel.locator("#rule-phrases").fill("good kitchen");
  await panel.locator("#rule-group").selectOption({label:"Kitchen"});
  await panel.locator("#rule-local-continue-to-ai").check();
  const condition = [{condition:"state",entity_id:"input_boolean.kitchen_ready",state:"on"}];
  await panel.locator("#rule-condition-host ha-selector").evaluate((selector, value) => {
    selector.value=value;
    selector.dispatchEvent(new CustomEvent("value-changed", {detail:{value},bubbles:true,composed:true}));
  }, condition);
  await panel.locator("#rule-save").click();
  await expect(panel.getByRole("heading", {name:"Conditional local", exact:true})).toBeVisible();
  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  const card = panel.locator(".request-rule-card").filter({hasText:"Conditional local"});
  await expect(card).toContainText("Kitchen");
  await expect(card).toContainText("continues to AI");
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-local-continue-to-ai")).toBeChecked();
  await expect(panel.locator("#rule-group")).toHaveValue(await panel.locator(".rule-group-row").getAttribute("data-group-id"));
  const saved = await panel.locator("#rule-condition-host ha-selector").evaluate((selector) => selector.value);
  expect(saved).toEqual(condition);
  await panel.locator(".rule-close").first().click();
  await expectHarnessClean(page, pageErrors);
});
