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
  await expect(panel.locator("#rule-action-sequence-host ha-selector")).toHaveJSProperty("value", []);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Add at least one action before saving this rule.");
  await panel.locator("#rule-action-sequence-host ha-selector").evaluate((selector) => {
    const value=[{action:"light.turn_on",target:{entity_id:"light.kitchen"}}];
    selector.value=value;
    selector.dispatchEvent(new CustomEvent("value-changed", {detail:{value},bubbles:true,composed:true}));
  });
  const condition = [{condition:"and",conditions:[{condition:"state",entity_id:"input_boolean.kitchen_ready",state:"on"},{condition:"not",conditions:[{condition:"state",entity_id:"input_boolean.kitchen_busy",state:"on"}]}]}];
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

test("Request Rule groups show global priorities and preserve order across moves and deletion", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".rule-groups summary").click();
  for (const name of ["Lighting", "Media"]) {
    await panel.locator("#rule-new-group-name").fill(name);
    await panel.locator("#rule-group-add").click();
  }
  for (const [name, group] of [["Lamp rule", "Lighting"], ["Music rule", "Media"]]) {
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill(name);
    await panel.locator("#rule-phrases").fill(name.toLowerCase());
    await panel.locator("#rule-group").selectOption({label:group});
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-save").click();
  }
  const section = (name) => panel.locator(".rule-group-section").filter({has:page.getByText(name,{exact:true})});
  await expect(panel.locator(".rule-group-section summary span")).toHaveText(["Ungrouped","Lighting","Media"]);
  await expect(section("Ungrouped").locator(".request-rule-card")).toContainText("Global priority 1");
  await expect(section("Lighting").locator(".request-rule-card")).toContainText("Global priority 2");
  await expect(section("Media").locator(".request-rule-card")).toContainText("Global priority 3");
  await section("Media").locator('.rule-move[data-direction="up"]').click();
  await expect(section("Media").locator(".request-rule-card")).toContainText("Global priority 2");
  await expect(section("Lighting").locator(".request-rule-card")).toContainText("Global priority 3");
  await section("Lighting").locator("summary").click();
  await expect(section("Lighting")).not.toHaveAttribute("open", "");
  await expect(section("Lighting").locator(".request-rule-card")).toHaveCount(1);
  await section("Lighting").locator("summary").click();
  const lighting = panel.locator(".rule-group-row").filter({has:page.locator('input[value="Lighting"]')});
  await lighting.locator(".rule-group-name").fill("Lights");
  await lighting.locator(".rule-group-rename").click();
  await expect(panel.locator(".rule-group-section summary span")).toHaveText(["Ungrouped","Lights","Media"]);
  await panel.locator(".rule-group-row").filter({has:page.locator('input[value="Lights"]')}).locator(".rule-group-delete").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".rule-group-section summary span")).toHaveText(["Ungrouped","Media"]);
  await expect(section("Ungrouped").locator(".request-rule-card")).toHaveCount(2);
  await expect(section("Ungrouped").locator(".request-rule-card").filter({hasText:"Lamp rule"})).toContainText("Global priority 3");
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await expect(panel.locator(".rule-group-section summary span")).toHaveText(["Ungrouped","Media"]);
  await expect(section("Media").locator(".request-rule-card")).toContainText("Global priority 2");
  await expectHarnessClean(page, pageErrors);
});
