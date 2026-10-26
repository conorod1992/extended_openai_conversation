import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function prepare(page, bundled, {baselineModel = "gpt-5.2", initialEffort = "xhigh"} = {}) {
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._modelCatalogData))).toBe(true);
  await panel.evaluate(async (host, {baselineModel, initialEffort}) => {
    const saved = await host._call("configuration", "save", {
      config:{chat_model:baselineModel, reasoning_effort:initialEffort}, revision:host._configData.revision,
    });
    const common = {supports_reasoning_effort:true, api:{responses:true, chat_completions:true}, function_calling:{responses:true, chat_completions:true}};
    const previous = {...common, reasoning:{supported:true, efforts:["none","low","medium","high","xhigh"]}};
    const current = {...common, reasoning:{supported:true, efforts:["minimal","low","medium","high"]}, recommended_profile:{reasoning_effort:"minimal"}};
    host._configData = {...saved, model_capabilities:baselineModel === "gpt-5.2" ? previous : current};
    host._configDataStale = false;
    host._draft = structuredClone(saved.config);
    host._draftTitle = saved.title;
    host._draftAgentId = host._agentId;
    host._result = host._configData;
    host._setConfigDirty(false);
    host._modelCatalogData = {requested_model:baselineModel, model_capabilities:host._configData.model_capabilities};
    host._render();
    const call = host.hass.callWS.bind(host.hass);
    window.modelReadBackend = call;
    window.modelReadLookups = {};
    window.releaseReadModels = {};
    window.returnedReadModels = {};
    host.hass.callWS = async message => {
      if (message.section === "configuration" && message.action === "save" && window.rejectModelReadSave) {
        window.rejectedModelReadSave = true;
        return {valid:false, errors:{max_tokens:"Invalid token value"}};
      }
      const result = await call(message);
      if (message.type.endsWith("/model_catalog")) {
        if (message.model === window.transientReadModel) {
          window.transientReadModelWaiting = true;
          await new Promise(resolve => {window.releaseTransientReadModel = resolve;});
        }
        if (["gpt-5", "gpt-5-mini"].includes(message.model)) {
          const count = window.modelReadLookups[message.model] = (window.modelReadLookups[message.model] || 0) + 1;
          if (count === 1) {
            await new Promise(resolve => {window.releaseReadModels[message.model] = resolve;});
            window.returnedReadModels[message.model] = true;
          }
        }
        const metadata = message.model === "gpt-5.2" ? previous : current;
        return {...result, model_capabilities:metadata, model_metadata:metadata};
      }
      if (message.section === "configuration" && message.action === "save" && window.holdModelReadSave) {
        window.modelReadSaveWaiting = true;
        await new Promise(resolve => {window.releaseModelReadSave = resolve;});
      }
      if (message.section === "configuration" && message.action === "get") {
        if (window.failModelRead) throw new Error("Configuration read unavailable");
        if (window.holdModelRead) {
          window.modelReadWaiting = true;
          await new Promise(resolve => {window.releaseModelRead = resolve;});
        }
      }
      return result;
    };
  }, {baselineModel, initialEffort});
  return panel;
}

async function startModel(page, panel, model = "gpt-5") {
  await panel.locator('[data-config="chat_model"]').fill(model);
  await panel.locator('[data-config="max_tokens"]').focus();
  await expect.poll(() => page.evaluate(model => Boolean(window.releaseReadModels[model]), model)).toBe(true);
}

async function releaseModel(page, panel, model = "gpt-5") {
  await page.evaluate(model => window.releaseReadModels[model](), model);
  await expect.poll(() => page.evaluate(model => Boolean(window.returnedReadModels[model]), model)).toBe(true);
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
}

async function saveExternally(panel, config) {
  await panel.evaluate(async (host, config) => {
    const current = browserHarness.getState().configuration;
    await window.modelReadBackend({
      type:"extended_openai_conversation_responses/management", section:"configuration", action:"save",
      entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId,
      config, revision:current.revision,
    });
  }, config);
}

async function readConfiguration(panel) {
  await panel.evaluate(async host => {
    host._configDataStale = true;
    host._cleanConfigSnapshots.clear();
    await host._loadConfigDraft();
    host._render();
  });
}

for (const bundled of [false, true]) test.describe(bundled ? "production model read intent" : "source model read intent", () => {
  for (const history of ["no read", "lookup then read", "read then lookup"]) for (const explicitChoice of [false,true]) test(`a successful ACK distinguishes derived reasoning from later explicit effort: ${history}, explicit=${explicitChoice}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await panel.locator('[data-config="reasoning_effort"]').selectOption("low");
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await startModel(page, panel);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await page.evaluate(() => {window.holdModelReadSave = true;});
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => Boolean(window.modelReadSaveWaiting))).toBe(true);
    if (history !== "read then lookup") await releaseModel(page, panel);
    if (history !== "no read") {
      await saveExternally(panel, {max_tokens:1400});
      await readConfiguration(panel);
    }
    if (history === "read then lookup") await releaseModel(page, panel);
    if (explicitChoice) await panel.locator('[data-config="reasoning_effort"]').selectOption("high");
    await page.evaluate(() => window.releaseModelReadSave());
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    const expected = explicitChoice ? "high" : "low";
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe(expected);
    await expect(panel.locator('[data-config="reasoning_effort"]')).toHaveValue(expected);
    // Check rereads the mounted controls. A stale selector must not resurrect
    // a programmatic Minimal value after the acknowledged draft was repaired.
    await panel.locator('[data-model-data="check"]').click();
    await expect(panel.locator('[data-model-data="check"]')).toBeEnabled();
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe(expected);
    expect(await panel.evaluate(host => host._configDirty)).toBe(explicitChoice);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("low");
    await expectHarnessClean(page, errors);
  });

  test("a newer persisted effort owns an unwritten reasoning field after derived work", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled, {baselineModel:"gpt-5", initialEffort:"low"});
    // Return to the saved model through a second authored choice while the
    // intermediate lookup is held. Only tokens are dirty in the actual Save.
    await page.evaluate(() => {window.transientReadModel = "gpt-5.2";});
    await panel.locator('[data-config="chat_model"]').fill("gpt-5.2");
    await panel.locator('[data-config="max_tokens"]').focus();
    await expect.poll(() => page.evaluate(() => Boolean(window.transientReadModelWaiting))).toBe(true);
    await startModel(page, panel);
    await page.evaluate(() => {window.transientReadModel = null; window.releaseTransientReadModel();});
    await panel.locator('[data-config="max_tokens"]').fill("1300");
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await page.evaluate(() => {window.holdModelReadSave = true;});
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => Boolean(window.modelReadSaveWaiting))).toBe(true);
    const submitted = await page.evaluate(() => browserHarness.calls.filter(message => message.section === "configuration" && message.action === "save").at(-1).config);
    expect(submitted).toEqual({max_tokens:1300});
    await releaseModel(page, panel);
    await saveExternally(panel, {reasoning_effort:"high"});
    await readConfiguration(panel);
    await page.evaluate(() => window.releaseModelReadSave());
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("high");
    await expect(panel.locator('[data-config="reasoning_effort"]')).toHaveValue("high");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    await panel.locator('[data-model-data="check"]').click();
    await expect(panel.locator('[data-model-data="check"]')).toBeEnabled();
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("high");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("high");
    await expectHarnessClean(page, errors);
  });

  for (const modelFirst of [true, false]) test(`an accepted read fulfils active model intent before its Save ACK, model completes first=${modelFirst}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await panel.locator('[data-config="reasoning_effort"]').selectOption("low");
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await startModel(page, panel);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await page.evaluate(() => {window.holdModelReadSave = true;});
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => Boolean(window.modelReadSaveWaiting))).toBe(true);
    await saveExternally(panel, {max_tokens:1400});
    await readConfiguration(panel);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("low");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
    if (modelFirst) await releaseModel(page, panel);
    await page.evaluate(() => window.releaseModelReadSave());
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    if (!modelFirst) await releaseModel(page, panel);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("low");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(1400);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("low");
    await expectHarnessClean(page, errors);
  });

  test("a failed read cannot retire pending model defaults", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await startModel(page, panel);
    await saveExternally(panel, {chat_model:"gpt-5", reasoning_effort:"low"});
    await page.evaluate(() => {window.failModelRead = true;});
    await expect(readConfiguration(panel)).rejects.toThrow("Configuration read unavailable");
    expect(await panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(true);
    await releaseModel(page, panel);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("minimal");
    await expectHarnessClean(page, errors);
  });

  test("a read of another saved model retains the local model preparation", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await startModel(page, panel);
    await saveExternally(panel, {max_tokens:1400});
    await readConfiguration(panel);
    expect(await panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(true);
    await releaseModel(page, panel);
    expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-5");
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("minimal");
    expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(1400);
    await expectHarnessClean(page, errors);
  });

  test("an accepted model read preserves a newer explicit reasoning choice", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await startModel(page, panel);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await panel.locator('[data-config="reasoning_effort"]').selectOption("high");
    await saveExternally(panel, {chat_model:"gpt-5", reasoning_effort:"low"});
    await readConfiguration(panel);
    await releaseModel(page, panel);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("high");
    expect(await panel.evaluate(host => host._configData.config.reasoning_effort)).toBe("low");
    expect(await panel.evaluate(host => host._configDirty)).toBe(true);
    await expectHarnessClean(page, errors);
  });

  test("a read satisfying an older model choice cannot retire a newer model intent", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await startModel(page, panel);
    await saveExternally(panel, {chat_model:"gpt-5", reasoning_effort:"low"});
    await page.evaluate(() => {window.holdModelRead = true;});
    await panel.evaluate(host => {
      host._configDataStale = true;
      host._cleanConfigSnapshots.clear();
      window.modelReadPromise = host._loadConfigDraft();
    });
    await expect.poll(() => page.evaluate(() => Boolean(window.modelReadWaiting))).toBe(true);
    await startModel(page, panel, "gpt-5-mini");
    await page.evaluate(async () => {window.holdModelRead = false; window.releaseModelRead(); await window.modelReadPromise;});
    expect(await panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(true);
    await page.evaluate(() => window.releaseReadModels["gpt-5"]());
    await releaseModel(page, panel, "gpt-5-mini");
    expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-5-mini");
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("minimal");
    expect(await panel.evaluate(host => host._modelCatalogData.requested_model)).toBe("gpt-5-mini");
    await expectHarnessClean(page, errors);
  });

  test("an invalid Save cannot abandon pending model defaults", async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await startModel(page, panel);
    await page.evaluate(() => {window.rejectModelReadSave = true;});
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => Boolean(window.rejectedModelReadSave))).toBe(true);
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    expect(await panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(true);
    await releaseModel(page, panel);
    expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe("minimal");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.chat_model)).toBe("gpt-5.2");
    await expectHarnessClean(page, errors);
  });
});
