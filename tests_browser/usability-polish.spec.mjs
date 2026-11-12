import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) {
  const extra = bundled ? "&bundle=1" : "";
  test(`Overview combines navigation and health without hiding problems (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("overview", extra));
    const panel = page.locator("extended-openai-management-panel");
    const parent = panel.getByRole("region", {name:"Configuration and health", exact:true});
    await expect(parent.getByRole("heading", {name:"Configuration & health", level:2, exact:true})).toBeVisible();
    await expect(parent.locator(".dashboard-card")).toHaveCount(8);
    await expect(parent.locator("#overview-health-details")).not.toHaveAttribute("open");
    for (const title of ["Assistant","Capabilities","Memory & Knowledge","Conversation history","Guest Mode","Web Search","Usage","Model Data"]) {
      const card = parent.locator(".dashboard-card").filter({has:page.getByRole("heading", {name:title, exact:true})});
      await expect(card.locator("button")).toBeVisible();
    }
    await panel.evaluate(host => {
      host._result.setup_health.provider_runtime.client_loaded = false;
      host._result.setup_health.web_search = {enabled:false};
      host._render();
    });
    await expect(parent.locator(".configuration-health-issues")).toContainText("Provider connection");
    await expect(parent.locator(".configuration-health-issues")).not.toContainText("Web Search");
    const summary = parent.locator("#overview-health-details > summary");
    await summary.focus(); await page.keyboard.press("Enter");
    await expect(parent.locator(".setup-health-check-error")).toContainText("not currently available");
    await expect(parent.getByRole("button", {name:"Run live test", exact:true})).toBeVisible();
    await expect(parent.locator(".setup-health-grid")).toContainText("Off by choice");
    await parent.getByRole("button", {name:"Run live test", exact:true}).click();
    await expect(panel.locator("#test-agent")).toBeVisible();
    expect(await page.evaluate(() => browserHarness.calls.some(call => call.action === "test_agent"))).toBe(false);
    await expectHarnessClean(page, errors);
  });

  test(`Model maintenance discloses recovery and updates only when relevant (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("assistant/model-responses", extra));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-model-data="check"]')).toBeVisible();
    await expect(panel.locator('[data-model-data="apply"]')).toBeHidden();
    await expect(panel.locator('[data-model-data="reset"]')).toBeHidden();
    await expect(panel.locator(".eoc-model-data-heading")).toContainText("Shared by all assistants");
    await panel.getByText("Recovery options", {exact:true}).click();
    await expect(panel.locator('[data-model-data="reset"]')).toBeVisible();
    await expect(panel.locator(".eoc-model-data-panel")).toContainText("for all assistants");
    await page.evaluate(() => {
      const send = browserHarness.hass.callWS.bind(browserHarness.hass);
      browserHarness.hass.callWS = async request => {
        const result = await send(request);
        return request.type.endsWith("/model_catalog") && request.action === "check"
          ? {...result, catalog_version:1, available_catalog_version:2, update_available:true} : result;
      };
    });
    await panel.locator('[data-model-data="check"]').click();
    await expect(panel.locator('[data-model-data="apply"]')).toBeVisible();
    await expect(panel.locator('[data-model-data="apply"]')).toHaveText("Apply v2 update");
  });

  test(`Prompt label and voice summary describe the editable settings (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("assistant/prompt-context", extra));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByLabel("System prompt", {exact:true})).toBeVisible();
    await expect(panel.locator('label[for="prompt-editor"]')).toBeVisible();
    await panel.locator('label[for="prompt-editor"]').click();
    await expect(panel.locator("#prompt-editor")).toBeFocused();
    await panel.evaluate(host => host._navigate("assistant", "voice"));
    const saved = await page.evaluate(() => browserHarness.getState().configuration.config.voice_scope_policy);
    await panel.locator('[data-config="voice_scope_policy"]').selectOption("shared");
    await expect(panel.getByText("Behaviour with these settings", {exact:true})).toBeVisible();
    await expect(panel.locator("#voice-current-summary")).toContainText("shared household data");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.voice_scope_policy)).toBe(saved);
  });

  test(`Request Rules use keyboard switches and active-mode guidance (${bundled})`, async ({page}) => {
    await page.setViewportSize({width:390,height:900});
    await page.goto(fixtureUrl("capabilities/request-rules", extra));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-enabled")).toHaveAttribute("role", "switch");
    await panel.getByText("Show default settings", {exact:true}).click();
    const normalize = panel.locator("#rules-default-word-forms");
    await expect(normalize).toHaveAttribute("role", "switch");
    await normalize.focus(); await page.keyboard.press("Space");
    await expect(normalize).not.toBeChecked();
    await expect(normalize).toBeFocused();
    await panel.locator("#rule-add").click();
    await panel.locator("#rule-advanced > summary").click();
    await expect(panel.locator("#rule-pattern-matching-help")).toBeHidden();
    await panel.locator("#rule-match").selectOption("sentence_pattern");
    await expect(panel.locator("#rule-pattern-matching-help")).toBeVisible();
    await expect(panel.locator("#rule-matching-controls")).toBeHidden();
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await expect(panel.locator("#rule-routing-match-help")).toContainText("complete command");
    await panel.locator("#rule-match").selectOption("contains");
    await expect(panel.locator("#rule-pattern-matching-help")).toBeHidden();
    await expect(panel.locator("#rule-routing-match-help")).toContainText("broader match");
    for (const id of ["rule-continue-to-ai","rule-reset","rule-continue-matching"]) {
      await expect(panel.locator(`#${id}`)).toHaveAttribute("role", "switch");
      await expect(panel.locator(`#${id}`)).toHaveAccessibleName(/\S+/);
    }
  });

  test(`Memory selection is navigation and collapsed metadata round-trips untouched (${bundled})`, async ({page}) => {
    const panel = await openDataCollection(page, "persistent", 10, bundled);
    await expect(panel.getByRole("navigation", {name:"Memory view"})).toBeVisible();
    const current = panel.locator('.memory-kind[aria-current="page"]');
    await expect(current).toHaveText("Long-term"); await expect(current).toBeEnabled();
    await page.evaluate(async () => {
      Object.assign(dataCollectionBackend.state.memories[1], {subject:"Bins",key:"bins.day",valid_from:"2026-09-01T00:00:00Z"});
      await browserHarness.panel._loadSection(true);
    });
    await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
    await expect(panel.locator("#memory-advanced")).not.toHaveAttribute("open");
    await expect(panel.getByText("Advanced metadata", {exact:true})).toBeVisible();
    await panel.locator("#memory-content").fill("Updated ordinary content");
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open",true);
    const update = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "update").at(-1));
    expect(update).toMatchObject({subject:"Bins",key:"bins.day",valid_from:"2026-09-01T00:00:00Z"});
    expect(update).not.toHaveProperty("clear_fields");
    await panel.locator('.memory-kind[data-kind="temporary"]').click();
    await expect(panel.locator('.memory-kind[aria-current="page"]')).toHaveText("Short-term");
  });

  test(`Capture privacy, switch state and retention consequences remain visible (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("usage-maintenance/request-debug", extra));
    const debug = page.locator("extended-openai-debug-panel");
    await expect(debug.getByText("Debug captures can contain private data", {exact:true})).toBeVisible();
    await expect(debug.getByText("What is captured?", {exact:true})).toBeVisible();
    const capture = debug.getByRole("switch", {name:"Request debugging", exact:true});
    await expect(capture).not.toBeChecked();
    await capture.focus(); await page.keyboard.press("Space");
    await expect(capture).toBeChecked();
    await expect(debug.getByText("Review captures for secrets before copying or sharing.", {exact:true})).toBeVisible();
    const panel = page.locator("extended-openai-management-panel");
    await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
    await expect(panel.locator("#config-retention")).toContainText("Saving Disabled immediately deletes existing detailed history");
    await expect(panel.locator("#config-retention")).toContainText("Overall usage totals are kept");
    await expect(panel.locator('[data-field="usage_request_retention_days"]')).toContainText("Individual provider calls");
    await expect(panel.locator('[data-field="usage_run_retention_days"]')).toContainText("Completed conversation turns");
  });

  test(`Memory matching keeps an inactive embedding model without duplicate guidance (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("data-memory/memory-settings", extra));
    const panel = page.locator("extended-openai-management-panel");
    const mode = panel.locator('[data-memory-config="memory_retrieval_mode"]');
    const model = panel.locator('[data-memory-config="memory_embedding_model"]');
    await mode.selectOption("hybrid");
    await expect(model).toBeEnabled();
    await model.fill("custom-embedding-model");
    await expect(panel.locator('[data-field="memory_embedding_model"]')).toContainText("keyword matching is used");
    await mode.selectOption("lexical");
    await expect(model).toBeDisabled();
    await expect(model).toHaveValue("custom-embedding-model");
    await expect(panel.locator('[data-field="memory_embedding_model"]')).toContainText("Saved but inactive");
    await expect(panel.locator('[data-eoc-guidance-generated="memory-embedding-dormant"],[data-eoc-guidance-generated="memory-hybrid-consequence"]')).toHaveCount(0);
  });
}
