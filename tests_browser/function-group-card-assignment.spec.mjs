import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Function Tool cards can move between groups and always available", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();

  let baselineGroup = panel.locator('.function-group-card[data-group-id="baseline-group"]');
  await baselineGroup.locator("summary").click();
  let card = baselineGroup.locator(".tool-card").filter({hasText: "baseline_tool"});
  let assignment = card.locator(".function-group-assignment");
  await expect(assignment).toHaveValue("baseline-group");
  await expect(assignment).toHaveAccessibleName("Function group for baseline_tool");

  await assignment.selectOption("");
  let alwaysCard = panel.locator(".function-group-card.always-card");
  card = alwaysCard.locator(".tool-card").filter({hasText: "baseline_tool"});
  await expect(card).toBeVisible();
  await expect(card.locator(".function-group-assignment")).toHaveValue("");
  await expect(baselineGroup.locator(".tool-card").filter({hasText: "baseline_tool"})).toHaveCount(0);

  await panel.locator("#add-group").click();
  await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#group-name").fill("Card assignment group");
  await panel.locator("#group-id").fill("card-assignment-group");
  await panel.locator("#group-description").fill("Group created for the per-card assignment browser test");
  await panel.locator("#group-save").click();

  alwaysCard = panel.locator(".function-group-card.always-card");
  card = alwaysCard.locator(".tool-card").filter({hasText: "baseline_tool"});
  assignment = card.locator(".function-group-assignment");
  await expect(assignment.locator('option[value="card-assignment-group"]')).toHaveText("Card assignment group");
  await assignment.selectOption("card-assignment-group");

  let targetGroup = panel.locator('.function-group-card[data-group-id="card-assignment-group"]');
  await targetGroup.locator("summary").click();
  card = targetGroup.locator(".tool-card").filter({hasText: "baseline_tool"});
  await expect(card).toBeVisible();
  await expect(card.locator(".function-group-assignment")).toHaveValue("card-assignment-group");
  await expect(alwaysCard.locator(".tool-card").filter({hasText: "baseline_tool"})).toHaveCount(0);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  targetGroup = panel.locator('.function-group-card[data-group-id="card-assignment-group"]');
  await expect(targetGroup).toBeVisible();
  await targetGroup.locator("summary").click();
  card = targetGroup.locator(".tool-card").filter({hasText: "baseline_tool"});
  await expect(card.locator(".function-group-assignment")).toHaveValue("card-assignment-group");

  await expectHarnessClean(page, pageErrors);
});
