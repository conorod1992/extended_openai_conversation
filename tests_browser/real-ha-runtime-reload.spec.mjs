import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const phase = process.env.REAL_HA_RUNTIME_RELOAD_PHASE;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");
test.skip(!["create", "verify"].includes(phase), "requires a runtime reload acceptance phase");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

const ruleName = "Browser reload runtime route";
const triggerPhrase = "browser reload runtime route";

async function openRequestRules(page) {
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  return panel;
}

test("browser-authored runtime route survives a Home Assistant reload", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  const panel = await openRequestRules(page);

  if (phase === "create") {
    await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);

    await panel.locator("#rule-name").fill(ruleName);
    await panel.locator("#rule-phrases").fill(triggerPhrase);
    await panel.locator("#rule-match").selectOption("equals");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("gpt-6-astra");
    await panel.locator("#rule-reasoning").selectOption("xhigh");
    await panel.locator("#rule-continue-to-ai").check();
    await expect(panel.locator("#rule-scope")).toBeEnabled();
    await panel.locator("#rule-scope").selectOption("conversation");
    await panel.locator("#rule-save").click();

    const card = panel.locator(".request-rule-card").filter({hasText: ruleName});
    await expect(card).toBeVisible();
    await expect(card).toContainText("gpt-6-astra");
  } else {
    const card = panel.locator(".request-rule-card").filter({hasText: ruleName});
    await expect(card).toBeVisible();
    await expect(card).toContainText("gpt-6-astra");

    await card.locator(".rule-edit").click();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#rule-name")).toHaveValue(ruleName);
    await expect(panel.locator("#rule-phrases")).toHaveValue(triggerPhrase);
    await expect(panel.locator("#rule-model")).toHaveValue("gpt-6-astra");
    await expect(panel.locator("#rule-reasoning")).toHaveValue("xhigh");
    await expect(panel.locator("#rule-continue-to-ai")).toBeChecked();
    await expect(panel.locator("#rule-scope")).toHaveValue("conversation");
  }

  await expectHarnessClean(page, pageErrors);
});
