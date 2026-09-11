import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function failNextManagementCall(page, section, action, message) {
  await page.evaluate(({section, action, message}) => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let failed = false;
    hass.callWS = async (request) => {
      if (!failed && request.section === section && request.action === action) {
        failed = true;
        throw new Error(message);
      }
      return original(request);
    };
  }, {section, action, message});
}

test("failed Memory save keeps the editor and draft intact, then retries cleanly", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill("Retry-safe browser memory");
  await panel.locator("#memory-category").fill("resilience");

  await failNextManagementCall(page, "memories", "add", "Transient memory save failure");
  await panel.locator("#memory-save").click();

  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#memory-error")).toHaveText("Unable to save memory: Transient memory save failure");
  await expect(panel.locator("#memory-content")).toHaveValue("Retry-safe browser memory");
  await expect(panel.locator("#memory-category")).toHaveValue("resilience");
  expect(await page.evaluate(() => window.browserHarness.getState().memories)).toHaveLength(1);

  await panel.locator("#memory-save").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);
  await expect(panel.getByText("Retry-safe browser memory", {exact: true})).toBeVisible();

  const memories = await page.evaluate(() => window.browserHarness.getState().memories);
  expect(memories).toHaveLength(2);
  expect(memories.filter((memory) => memory.content === "Retry-safe browser memory")).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});

test("failed Request Rule save preserves the draft and backend, then retries cleanly", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("Retry-safe browser rule");
  await panel.locator("#rule-phrases").fill("retry safe route");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("request");

  await failNextManagementCall(page, "request_rules", "create", "Transient Request Rule save failure");
  await panel.locator("#rule-save").click();

  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#rule-error")).toHaveText("Transient Request Rule save failure");
  await expect(panel.locator("#rule-name")).toHaveValue("Retry-safe browser rule");
  await expect(panel.locator("#rule-phrases")).toHaveValue("retry safe route");
  expect(await page.evaluate(() => window.browserHarness.getState().requestRules.rules)).toHaveLength(1);

  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  await expect(panel.getByRole("heading", {name: "Retry-safe browser rule", exact: true})).toBeVisible();

  const rules = await page.evaluate(() => window.browserHarness.getState().requestRules.rules);
  expect(rules).toHaveLength(2);
  expect(rules.filter((rule) => rule.name === "Retry-safe browser rule")).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});
