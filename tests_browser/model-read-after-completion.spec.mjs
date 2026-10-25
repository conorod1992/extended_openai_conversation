import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function completedModel(page, bundled, choice) {
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._modelCatalogData))).toBe(true);
  await panel.evaluate(async host => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    const saved = await host._call("configuration", "save", {
      config:{chat_model:"gpt-5.2", reasoning_effort:"xhigh", service_tier:"default", shorten_tool_call_id:false},
      revision:host._configData.revision,
    });
    const common = {supports_reasoning_effort:true, api:{responses:true, chat_completions:true}, function_calling:{responses:true, chat_completions:true}};
    const previous = {...common, reasoning:{supported:true, efforts:["none","low","medium","high","xhigh"]}};
    host._configData = {...saved, model_capabilities:previous};
    host._configDataStale = false;
    host._draft = structuredClone(saved.config);
    host._draftTitle = saved.title;
    host._draftAgentId = host._agentId;
    host._result = host._configData;
    host._setConfigDirty(false);
    host._modelCatalogData = {requested_model:"gpt-5.2", model_capabilities:previous};
    host._render();
    const call = host.hass.callWS.bind(host.hass);
    window.completedModelBackend = call;
    window.completedModelSources = [];
    host.hass.callWS = async message => {
      const result = await call(message);
      if (!message.type.endsWith("/model_catalog")) return result;
      const metadata = message.model === "gpt-5.2" ? previous : {
        ...common, reasoning:{supported:true, efforts:["minimal","low","medium","high"]},
        recommended_profile:{reasoning_effort:"minimal"},
      };
      return {...result, model_capabilities:metadata, model_metadata:metadata};
    };
  });
  await panel.locator('[data-config="chat_model"]').fill("gpt-5");
  await panel.locator('[data-config="max_tokens"]').focus();
  await expect.poll(() => panel.evaluate(host => host._draft.reasoning_effort)).toBe("minimal");
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
  await panel.evaluate(host => host._navigate("assistant", "model-responses"));
  const effort = panel.locator('[data-config="reasoning_effort"]');
  if (choice !== "automatic") {
    await effort.selectOption("high");
    if (choice === "explicit minimal") await effort.selectOption("minimal");
  }
  await panel.evaluate(host => {window.completedModelIntent = host._eocModelIntent;});
  return panel;
}

for (const bundled of [false, true]) test.describe(bundled ? "production completed model reads" : "source completed model reads", () => {
  for (const replacements of [0,1,2]) for (const choice of ["automatic","explicit high","explicit minimal"]) test(`completed defaults keep read provenance across ${replacements} replacements, choice=${choice}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await completedModel(page, bundled, choice);
    for (let index = 0; index < replacements; index++) {
      await panel.evaluate(host => {
        const parent = host.parentElement, hass = host.hass;
        window.completedModelSources.push(host);
        host.remove();
        const next = document.createElement("extended-openai-management-panel");
        parent.append(next);
        next.hass = hass;
      });
      await expect.poll(() => panel.evaluate(host => host._draft?.chat_model)).toBe("gpt-5");
      expect(await panel.evaluate(host => host._eocModelIntent === window.completedModelIntent)).toBe(true);
      expect(await page.evaluate(() => window.completedModelSources.every(source => !source._eocModelIntent))).toBe(true);
    }
    await panel.evaluate(async host => {
      const current = browserHarness.getState().configuration;
      const result = await window.completedModelBackend({
        type:"extended_openai_conversation_responses/management", section:"configuration", action:"save",
        entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId,
        config:{chat_model:"gpt-5", reasoning_effort:"low"}, revision:current.revision,
      });
      window.completedModelReadRevision = result.revision;
    });
    // The real catalogue action performs the ordinary full configuration read.
    // No subject Save/ACK can later hide a timing-dependent derived delta.
    await panel.locator('[data-model-data="reset"]').click();
    await expect.poll(() => panel.evaluate(host => host._configData.revision === window.completedModelReadRevision && !host._configDataStale)).toBe(true);
    const expected = choice === "automatic" ? "low" : choice === "explicit high" ? "high" : "minimal";
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe(expected);
    await expect(panel.locator('[data-config="reasoning_effort"]')).toHaveValue(expected);
    expect(await panel.evaluate(host => host._eocDirtyConfigKeys.has("reasoning_effort"))).toBe(choice !== "automatic");
    expect(await panel.evaluate(host => host._configData.config.reasoning_effort)).toBe("low");
    await panel.locator('[data-model-data="check"]').click();
    await expect(panel.locator('[data-model-data="check"]')).toBeEnabled();
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe(expected);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("low");
    await expectHarnessClean(page, errors);
  });
});
