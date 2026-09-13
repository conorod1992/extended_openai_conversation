import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the dedicated genuine Home Assistant backend bridge");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

test("browser-created request rule is ready for live conversation execution", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(realFixtureUrl("capabilities/request-rules"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);

  await panel.locator("#rule-name").fill("Browser live runtime route");
  await panel.locator("#rule-phrases").fill("browser runtime route");
  await panel.locator("#rule-match").selectOption("equals");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-6-astra");
  await panel.locator("#rule-reasoning").selectOption("xhigh");

  // Equals routing rules default to a consumed routing command. This journey
  // specifically needs the matched utterance to continue to the provider so
  // that the browser-authored route can be observed on the live wire request.
  await panel.locator("#rule-continue-to-ai").check();
  await expect(panel.locator("#rule-scope")).toBeEnabled();
  await panel.locator("#rule-scope").selectOption("conversation");
  await panel.locator("#rule-save").click();

  await expect(panel.getByRole("heading", {name: "Browser live runtime route", exact: true})).toBeVisible();

  // Re-open the page once so the browser independently proves that HA accepted
  // and persisted the rule before the Python side exercises the live agent.
  await page.goto(realFixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  const card = panel.locator(".request-rule-card").filter({hasText: "Browser live runtime route"});
  await expect(card).toBeVisible();
  await expect(card).toContainText("gpt-6-astra");
  await expectHarnessClean(page, pageErrors);
});
