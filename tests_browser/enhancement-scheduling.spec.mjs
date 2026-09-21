import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

test("guidance responds to its dependencies without rescanning for unrelated draft changes", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const counts = await page.evaluate(async () => {
    const {enhanceConfigurationGuidance} = await import("/custom_components/extended_openai_conversation_responses/frontend/management-configuration-guidance.js");
    const {panel} = window.browserHarness;
    enhanceConfigurationGuidance(panel);
    const root = panel.shadowRoot, original = root.querySelector;
    let scans = 0;
    root.querySelector = function(selector) {
      if (selector === "#config-model .form-grid") scans++;
      return original.call(this, selector);
    };
    try {
      panel._draft.prompt = "Unrelated prompt edit";
      enhanceConfigurationGuidance(panel);
      const unrelated = scans;
      panel._draft.api_mode = "auto";
      panel._configurationGuidanceAgentId = panel._agentId;
      panel._configurationGuidance = {effective_api_mode:"responses"};
      enhanceConfigurationGuidance(panel);
      const runtime = scans;
      const runtimeUpdated = root.querySelector('[data-eoc-guidance-generated="api-mode-auto"]')?.textContent.includes("Responses API");
      enhanceConfigurationGuidance(panel);
      const repeated = scans;
      panel._configData.model_capabilities = {supports_temperature:false};
      panel._result.model_capabilities = panel._configData.model_capabilities;
      enhanceConfigurationGuidance(panel);
      const capabilities = scans;
      panel._eocSearchResultsRevision++;
      enhanceConfigurationGuidance(panel);
      return {unrelated, runtime, repeated, capabilities, search:scans, runtimeUpdated};
    } finally { root.querySelector = original; }
  });
  expect(counts).toEqual({unrelated:0, runtime:0, repeated:0, capabilities:0, search:0, runtimeUpdated:true});
});

for (const query of ["", "conversation timeout"]) test(`unchanged renders skip enhancement queries and content replacement invalidates them (query: ${query})`, async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const result = await page.evaluate((query) => {
    const {panel} = window.browserHarness;
    panel._settingsSearchQuery = query;
    panel._render();
    const root = panel.shadowRoot;
    const queries = [];
    const original = root.querySelector;
    root.querySelector = function(selector) {
      queries.push(selector);
      return original.call(this, selector);
    };
    const count = () => Object.fromEntries([
      ["guidance", '#config-model .form-grid'],
      ["clarity", 'style[data-eoc-configuration-clarity]'],
    ].map(([name, selector]) => [name, queries.filter((query) => query === selector).length]));
    try {
      for (let i = 0; i < 10; i++) panel._render();
      const unchanged = count();
      queries.length = 0;
      panel._error = "Replacement invalidation probe";
      panel._render();
      panel._error = null;
      panel._render();
      return {unchanged, replaced:count(), restored:Boolean(root.querySelector('[data-config="chat_model"]'))};
    } finally { root.querySelector = original; }
  }, query);
  console.log("Enhancement query counts", result);
  expect(result.unchanged).toEqual({guidance:0, clarity:0});
  expect(result.replaced.guidance).toBe(0);
  expect(result.restored).toBe(true);
});
