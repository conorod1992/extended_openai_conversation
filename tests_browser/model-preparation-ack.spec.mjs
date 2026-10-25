import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function prepare(page, bundled) {
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._modelCatalogData))).toBe(true);
  await panel.evaluate(async host => {
    // Seed through the fixture's registered configuration handler so subsequent
    // full and partial acknowledgements share the same saved baseline.
    const saved = await host._call("configuration", "save", {
      config:{chat_model:"gpt-5.2", reasoning_effort:"xhigh"}, revision:host._configData.revision,
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
    Object.assign(host._selectedAgent(), saved.agent);
    host._modelCatalogData = {requested_model:"gpt-5.2", model_capabilities:previous};
    host._render();
    const call = host.hass.callWS.bind(host.hass);
    window.ackBackendCall = call;
    window.ackModelLookups = 0;
    window.resumedAckModelGates = [];
    window.ackGroupBaseline = JSON.stringify(host._draft.function_groups);
    host.hass.callWS = async message => {
      const result = await call(message);
      if (message.type.endsWith("/model_catalog")) {
        if (message.model === "gpt-5") {
          if (++window.ackModelLookups === 1) {
            window.ackModelWaiting = true;
            await new Promise(resolve => {window.releaseAckModel = resolve;});
            window.ackModelReturned = true;
          } else if (window.holdResumedAckModel) {
            await new Promise(resolve => {window.resumedAckModelGates.push(resolve);});
          }
        }
        const metadata = message.model === "gpt-5.2" ? previous : {
          ...common, reasoning:{supported:true, efforts:["minimal","low","medium","high"]},
          recommended_profile:{reasoning_effort:"minimal"},
        };
        return {...result, model_capabilities:metadata, model_metadata:metadata};
      }
      if (message.section === "configuration" && message.action === "save" && window.holdOlderConfigurationSave) {
        window.olderConfigurationSaveWaiting = true;
        await new Promise(resolve => {window.releaseOlderConfigurationSave = resolve;});
      }
      return result;
    };
  });
  return panel;
}

async function beginModelChange(page, panel, explicitEffort, enterParameters = true) {
  await panel.locator('[data-config="chat_model"]').fill("gpt-5");
  await panel.locator('[data-config="max_tokens"]').focus();
  await expect.poll(() => page.evaluate(() => Boolean(window.ackModelWaiting))).toBe(true);
  if (enterParameters) {
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await expect(panel.locator('[data-config="reasoning_effort"]')).toBeEnabled();
    if (explicitEffort) await panel.locator('[data-config="reasoning_effort"]').selectOption(explicitEffort);
  }
}

async function finishModelChange(page, panel, expected) {
  await page.evaluate(() => window.releaseAckModel());
  await expect.poll(() => page.evaluate(() => Boolean(window.ackModelReturned))).toBe(true);
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
  expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-5");
  expect(await panel.evaluate(host => host._draft.reasoning_effort)).toBe(expected);
  expect(await panel.evaluate(host => JSON.stringify(host._draft.function_groups) === window.ackGroupBaseline)).toBe(true);
  await panel.evaluate(host => host._navigate("assistant", "model-responses"));
  await expect(panel.locator('[data-config="reasoning_effort"]')).toHaveValue(expected);
}

for (const bundled of [false, true]) test.describe(bundled ? "production model preparation acknowledgements" : "source model preparation acknowledgements", () => {
  for (const explicitEffort of [null, "high"]) test(`an unrelated Function toggle preserves pending model preparation, explicit effort=${explicitEffort}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await beginModelChange(page, panel, explicitEffort);
    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    await panel.getByText("Show member functions", {exact:true}).click();
    await panel.locator(".tool-enabled").first().uncheck();
    await expect.poll(() => panel.evaluate(host => host._configData.config.functions[0].enabled)).toBe(false);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.functions[0].enabled)).toBe(false);
    await finishModelChange(page, panel, explicitEffort || "minimal");
    expect(await panel.evaluate(host => host._draft.functions[0].enabled)).toBe(false);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.chat_model)).toBe("gpt-5.2");
    expect(await page.evaluate(() => browserHarness.calls.filter(message => message.section === "tools" && message.action === "set_enabled").length)).toBe(1);
    await expectHarnessClean(page, errors);
  });

  for (const explicitEffort of [null, "high"]) test(`an older full Save acknowledgement preserves the later model edit, explicit effort=${explicitEffort}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    await page.evaluate(() => {window.holdOlderConfigurationSave = true;});
    await panel.locator('[data-config="max_tokens"]').fill("1300");
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => Boolean(window.olderConfigurationSaveWaiting))).toBe(true);
    await beginModelChange(page, panel, null, false);
    await page.evaluate(() => window.releaseOlderConfigurationSave());
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    expect(await panel.evaluate(host => host._configData.config.chat_model)).toBe("gpt-5.2");
    expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(1300);
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await expect(panel.locator('[data-config="reasoning_effort"]')).toBeEnabled();
    if (explicitEffort) await panel.locator('[data-config="reasoning_effort"]').selectOption(explicitEffort);
    await finishModelChange(page, panel, explicitEffort || "minimal");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.chat_model)).toBe("gpt-5.2");
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(1300);
    await expectHarnessClean(page, errors);
  });

  for (const boundary of ["normal", "accepted read", "reattach"]) test(`an acknowledgement committing the active model intent cancels its late recommendation, boundary=${boundary}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await prepare(page, bundled);
    // Low is explicitly dirty before this model operation starts, so its Save
    // really submits Low. There is no later reasoning revision to hide an
    // incorrect rebind that reapplies the delayed Minimal recommendation.
    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    await panel.locator('[data-config="reasoning_effort"]').selectOption("low");
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await beginModelChange(page, panel);
    if (boundary !== "normal") await page.evaluate(() => {window.holdOlderConfigurationSave = true;});
    await panel.locator("#save-config").click();
    if (boundary === "accepted read") {
      await expect.poll(() => page.evaluate(() => Boolean(window.olderConfigurationSaveWaiting))).toBe(true);
      await panel.evaluate(async host => {
        // Another writer advances the real persisted fixture to revision C.
        // The forced accepted read must remain authoritative over Save B.
        const current = browserHarness.getState().configuration;
        const saved = await window.ackBackendCall({
          type:"extended_openai_conversation_responses/management", section:"configuration", action:"save",
          entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId,
          config:{max_tokens:1400}, revision:current.revision,
        });
        window.ackNewerRevision = saved.revision;
        host._configDataStale = true;
        host._cleanConfigSnapshots.clear();
        await host._loadConfigDraft();
        host._render();
      });
      expect(await panel.evaluate(host => host._configData.revision === window.ackNewerRevision)).toBe(true);
      await page.evaluate(() => window.releaseOlderConfigurationSave());
    } else if (boundary === "reattach") {
      await expect.poll(() => page.evaluate(() => Boolean(window.olderConfigurationSaveWaiting))).toBe(true);
      await panel.evaluate(host => {
        window.ackDetachedPanel = host;
        window.ackPanelParent = host.parentElement;
        window.ackSubmittedIntent = host._eocModelIntent;
        window.ackSubmittedOperation = host._eocModelChangeToken;
        window.holdResumedAckModel = true;
        host.remove();
      });
      await page.evaluate(() => window.releaseAckModel());
      await expect.poll(() => page.evaluate(() => Boolean(window.ackDetachedPanel._eocPendingModelPreparation?.running))).toBe(false);
      await page.evaluate(() => window.ackPanelParent.append(window.ackDetachedPanel));
      await expect.poll(() => page.evaluate(() => window.resumedAckModelGates.length)).toBeGreaterThan(0);
      expect(await panel.evaluate(host => host._eocModelIntent === window.ackSubmittedIntent)).toBe(true);
      expect(await panel.evaluate(host => host._eocModelChangeToken > window.ackSubmittedOperation)).toBe(true);
      await page.evaluate(() => window.releaseOlderConfigurationSave());
    }
    await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving))).toBe(false);
    expect(await panel.evaluate(host => host._configData.config.chat_model)).toBe("gpt-5");
    expect(await panel.evaluate(host => host._configData.config.reasoning_effort)).toBe("low");
    if (boundary === "reattach") await page.evaluate(() => {
      window.holdResumedAckModel = false;
      for (const release of window.resumedAckModelGates) release();
    });
    await finishModelChange(page, panel, "low");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config.reasoning_effort)).toBe("low");
    if (boundary === "accepted read") {
      expect(await panel.evaluate(host => host._configData.revision === window.ackNewerRevision)).toBe(true);
      expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(1400);
    }
    await expectHarnessClean(page, errors);
  });
});
