import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

test("configuration and Knowledge return final markup before any enhancement can run", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator("extended-openai-management-panel [data-config='api_mode']")).toBeVisible();
  const result = await page.evaluate(async (base) => {
    const {renderConfiguration} = await import(`${base}agent-config-editor-base.js`);
    const {renderMemorySettings} = await import(`${base}memory-settings-ui.js`);
    const {modelDataControls} = await import(`${base}model-catalog.js`);
    const {panel} = window.browserHarness;
    const owner = {_e:panel._e.bind(panel),_titleCase:panel._titleCase.bind(panel),_configSections:["general","conversation"],_result:{config:{api_mode:"auto",conversation_continuity:"ha_default",memory_retrieval_mode:"hybrid"},defaults:{api_mode:"auto",conversation_continuity:"ha_default"},options:{api_mode:[{value:"auto",label:"Auto"}],memory_retrieval_mode:[{value:"hybrid",label:"Hybrid"}]}}};
    // Detached markup is inspected synchronously, without panel render hooks or a microtask.
    const host = document.createElement("div");
    host.innerHTML = renderConfiguration(owner) + renderMemorySettings(owner) + modelDataControls(owner);
    const initial = {text:host.textContent,selected:host.querySelector('[data-memory-config="memory_retrieval_mode"] option:checked')?.textContent,disabled:host.querySelector('[data-model-data="apply"]').disabled,hidden:host.querySelector('[data-model-data="apply"]').closest('.eoc-model-data-action').hidden};
    owner._modelCatalogData = {catalog_version:2,available_catalog_version:3,update_available:true};
    host.innerHTML = modelDataControls(owner);
    initial.available = !host.querySelector('[data-model-data="apply"]').closest('.eoc-model-data-action').hidden && !host.querySelector('[data-model-data="apply"]').disabled;
    initial.update = host.querySelector('[data-model-data="apply"]').textContent;
    await panel._navigate("data-memory", "knowledge");
    const old = panel._result;
    try {
      panel._result = {sources:[{source_id:"direct",title:"Notes",enabled:false}]};
      initial.knowledge = panel._knowledge();
    } finally { panel._result = old; }
    return initial;
  }, frontend);
  expect(result.text).toContain("Recommended default: Automatic (Auto)");
  expect(result.text).toContain("Default: Use Home Assistant sessions");
  expect(result.text).toContain("Requires embeddings");
  expect(result.text).not.toContain("Default: Ha Default");
  expect(result.selected).toBe("Semantic + keyword matching (Hybrid)");
  expect(result).toMatchObject({disabled:true,hidden:true,available:true,update:"Apply v3 update"});
  expect(result.knowledge).toContain("Unavailable");
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#knowledge-dialog")).toHaveCount(0);
  await panel.locator("#add-source").click();
  await expect(panel.locator("#knowledge-source-enabled")).toBeChecked();
  await expectHarnessClean(page, errors);
});

test("direct Request Rule testers bind once through repeated binding and normal rerenders", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#eoc-rule-live-test")).toBeVisible();
  const markup = await page.evaluate(async (base) => {
    const {renderRequestRules, bindRequestRules} = await import(`${base}request-rules-ui.js`);
    const {panel,hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.directCalls = [];
    hass.callWS = async (message) => { window.directCalls.push(message); return original(message); };
    panel._render(); panel._render();
    bindRequestRules(panel); bindRequestRules(panel);
    return renderRequestRules(panel);
  }, frontend);
  expect(markup).toContain("Preview rule match (safe)");
  expect(markup).toContain("Safe preview — nothing executes");
  expect(markup).toContain("Run full request (live)");
  await panel.locator("#eoc-rule-live-test summary").click();
  await panel.locator("#eoc-rule-live-text").fill("hello");
  await panel.locator("#eoc-rule-live-run").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => window.directCalls.filter((m) => m.section === "request_rules" && m.action === "test").length)).toBe(1);
  await expect(panel.locator("#eoc-rule-live-run")).toBeEnabled();
  await expectHarnessClean(page, errors);
});
