import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

test("an empty tool-call limit is rejected and explicit zero is saved", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".general-advanced > summary").click();
  const limit = panel.locator('[data-config="max_function_calls_per_conversation"]');
  await expect(limit).toHaveValue("8");
  await limit.fill("");
  await panel.locator("#save-config").click();
  await expect(panel.locator('[data-error="max_function_calls_per_conversation"]')).toHaveText("Enter a number.");
  await expect(limit).toBeFocused();
  expect(await page.evaluate(() => browserHarness.calls.some(call => call.section === "configuration" && call.action === "save"))).toBe(false);

  await limit.fill("0");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.max_function_calls_per_conversation)).toBe(0);
});

test("Guest Mode distinguishes an incomplete start from an intentionally empty one", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  const start = panel.locator("#guest-start");
  await panel.locator("#guest-indefinite").check();
  await start.fill("2030-11-20T18:30");
  await start.focus();
  await start.press("Delete");
  expect(await start.evaluate(input => input.validity.badInput)).toBe(true);
  await panel.locator("#guest-update").click();
  await expect(panel.locator("#toast")).toContainText("Complete the start time");
  expect(await page.evaluate(() => browserHarness.calls.some(call => call.section === "guest_mode" && call.action === "update"))).toBe(false);

  await start.fill("");
  await panel.locator("#guest-update").click();
  await expect.poll(() => page.evaluate(() => browserHarness.calls.find(call => call.section === "guest_mode" && call.action === "update")?.indefinite)).toBe(true);
  const update = await page.evaluate(() => browserHarness.calls.find(call => call.section === "guest_mode" && call.action === "update"));
  expect(update).not.toHaveProperty("active_from");
});

test("an unfinished Voice assignment is still a draft after saving another policy", async ({page}) => {
  await page.goto(fixtureUrl("assistant/voice"));
  const panel = page.locator("extended-openai-management-panel");
  await page.evaluate(() => {
    const state = browserHarness.getState();
    state.configuration.config.voice_device_mappings = {office:"shared:household"};
    localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
  });
  await page.goto(fixtureUrl("assistant/voice"));
  const policy = panel.locator("#config-voice_scope_policy");
  await policy.selectOption("device_mapping");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(1);
  await panel.locator("#add-voice-mapping").click();
  await policy.selectOption("shared");
  await expect(panel.locator("#add-voice-mapping")).toBeHidden();
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.voice_scope_policy)).toBe("shared");
  const saved = await page.evaluate(() => browserHarness.calls.find(call => call.section === "configuration" && call.action === "save")?.config);
  expect(saved).not.toHaveProperty("voice_device_mappings");

  await policy.selectOption("device_mapping");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await panel.locator("#save-config").click();
  await expect(panel.locator("#configuration-errors")).toContainText("remove the empty assignment before saving");
});

test("renaming a bound Azure deployment keeps its explicit reasoning effort", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._modelCatalogData))).toBe(true);
  await panel.evaluate(async host => {
    const config = {chat_model:"production-luna", azure_model:"gpt-6-luna", reasoning_effort:"high"};
    const saved = await host._call("configuration", "save", {config, revision:host._configData.revision});
    const metadata = {reasoning:{supported:true, efforts:["low", "high"]}, recommended_profile:{reasoning_effort:"low"}};
    host._configData = {...saved, api_provider:"azure", model_capabilities:metadata};
    host._draft = structuredClone(saved.config);
    host._result = host._configData;
    host._modelCatalogData = {requested_model:"gpt-6-luna", requested_provider:"azure", model_capabilities:metadata};
    const call = host._hass.callWS.bind(host._hass);
    host._hass.callWS = message => message.type.endsWith("/model_catalog") && message.action === "lookup"
      ? Promise.resolve({model_metadata:metadata, model_capabilities:metadata}) : call(message);
    host._render();
  });
  const deployment = panel.locator('[data-config="chat_model"]');
  await expect(deployment).toHaveValue("production-luna");
  await deployment.fill("secondary-luna");
  await panel.locator('[data-config="max_tokens"]').focus();
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.chat_model)).toBe("secondary-luna");
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("high");
});

test("a stale Azure binding does not determine OpenAI model options", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._modelCatalogData))).toBe(true);
  await panel.evaluate(async host => {
    const config = {chat_model:"gpt-5.2", azure_model:"gpt-5", reasoning_effort:"high"};
    const saved = await host._call("configuration", "save", {config, revision:host._configData.revision});
    const metadata = {reasoning:{supported:true, efforts:["low", "high"]}, recommended_profile:{reasoning_effort:"low"}};
    host._configData = {...saved, api_provider:"openai", model_capabilities:metadata};
    host._draft = structuredClone(saved.config);
    host._result = host._configData;
    host._modelCatalogData = {requested_model:"gpt-5.2", requested_provider:"openai", model_capabilities:metadata};
    const call = host._hass.callWS.bind(host._hass);
    window.openaiModelLookups = [];
    host._hass.callWS = message => {
      if (message.type.endsWith("/model_catalog") && message.action === "lookup") {
        window.openaiModelLookups.push(message.model);
        return Promise.resolve({model_metadata:metadata, model_capabilities:metadata});
      }
      return call(message);
    };
    host._render();
  });
  const model = panel.locator('[data-config="chat_model"]');
  await expect(model).toHaveValue("gpt-5.2");
  await model.fill("gpt-5.6");
  await panel.locator('[data-config="max_tokens"]').focus();
  await expect.poll(() => page.evaluate(() => window.openaiModelLookups)).toEqual(["gpt-5.6"]);
  await expect.poll(() => panel.evaluate(host => host._draft.reasoning_effort)).toBe("low");
});
