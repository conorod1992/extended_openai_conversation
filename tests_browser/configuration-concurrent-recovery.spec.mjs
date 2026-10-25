import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function prepare(page, bundled, {sourceRetention, sourceModel = false} = {}) {
  await page.goto(fixtureUrl("assistant/prompt-context", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="prompt"]')).toBeVisible();
  await panel.evaluate(host => { host.hass = {...host.hass, user:{id:"admin", is_admin:true}}; });
  await panel.locator('[data-config="prompt"]').fill("Original retained prompt draft");
  if (sourceModel) {
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await panel.locator('[data-config="chat_model"]').fill("gpt-4o");
    await panel.locator('[data-config="max_tokens"]').focus();
    await expect.poll(() => panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
  }
  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  if (sourceRetention !== undefined) await panel.locator('[data-config="usage_request_retention_days"]').selectOption(String(sourceRetention));
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.partialMergeState = {source:host, reads:0, entered:false, intent:host._eocModelIntent};
    host.hass.callWS = async request => {
      if (request.section === "configuration" && request.action === "get" && ++partialMergeState.reads === 1) {
        partialMergeState.entered = true;
        await new Promise(resolve => {partialMergeState.release = resolve;});
      }
      const result = await original(request);
      if (request.section === "configuration" && result.options?.usage_request_retention_days) {
        // The fixture has only two durations; include the backend's supported
        // Disabled choice to author a different concurrent retention change.
        result.options.usage_request_retention_days = [...result.options.usage_request_retention_days, {value:0, label:"Disabled"}];
      }
      return result;
    };
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
  await expect.poll(() => page.evaluate(() => partialMergeState.entered)).toBe(true);
  return panel;
}

for (const bundled of [false, true]) for (const sourceModel of [false, true]) test(`retained prompt joins target Retention edits during recovery, source model=${sourceModel} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await prepare(page, bundled, {sourceModel});
  await panel.locator('[data-config="usage_request_retention_days"]').selectOption("7");
  await page.evaluate(() => partialMergeState.release());
  await expect.poll(() => panel.evaluate(host => host._draft.prompt)).toBe("Original retained prompt draft");
  expect(await panel.evaluate(host => host._draft.usage_request_retention_days)).toBe(7);
  expect(await page.evaluate(() => partialMergeState.source._configDirty)).toBe(false);
  if (sourceModel) {
    expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-4o");
    expect(await panel.evaluate(host => host._eocModelIntent === partialMergeState.intent)).toBe(true);
  }
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Original retained prompt draft");
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  const writes = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
  expect(writes).toHaveLength(1);
  expect(writes[0].config).toMatchObject({prompt:"Original retained prompt draft", usage_request_retention_days:7});
  if (sourceModel) expect(writes[0].config.chat_model).toBe("gpt-4o");
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`equal retained and target edits merge without review (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await prepare(page, bundled, {sourceRetention:7});
  await panel.locator('[data-config="usage_request_retention_days"]').selectOption("7");
  await page.evaluate(() => partialMergeState.release());
  await expect.poll(() => panel.evaluate(host => host._draft.prompt)).toBe("Original retained prompt draft");
  await expect(panel.getByRole("heading", {name:"Recovered unsaved configuration"})).toHaveCount(0);
  expect(await panel.evaluate(host => host._draft.usage_request_retention_days)).toBe(7);
  expect(await page.evaluate(() => partialMergeState.source._configDirty)).toBe(false);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`conflicting Retention drafts remain accessible until explicit discard (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await prepare(page, bundled, {sourceRetention:7});
  await panel.locator('[data-config="usage_request_retention_days"]').selectOption("0");
  await page.evaluate(() => partialMergeState.release());
  const copy = panel.getByRole("heading", {name:"Recovered unsaved configuration"});
  await expect(copy).toBeVisible();
  const values = panel.getByLabel("Recovered configuration values");
  expect(JSON.parse(await values.inputValue())).toEqual({config:{prompt:"Original retained prompt draft", usage_request_retention_days:7}});
  expect(await panel.evaluate(host => host._draft.usage_request_retention_days)).toBe(0);
  expect(await page.evaluate(() => partialMergeState.source._configDirty)).toBe(true);
  expect(await panel.evaluate(host => [...host._unsavedState.destinations()])).toContain("assistant/prompt-context");
  await panel.getByRole("button", {name:"Keep draft for later"}).click();
  await panel.evaluate(host => host._render());
  await expect(copy).toHaveCount(0);
  expect(await page.evaluate(() => partialMergeState.source._configDirty)).toBe(true);
  await panel.evaluate(host => host._navigate("guide"));
  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  await expect(copy).toBeVisible();
  await panel.getByRole("button", {name:"Discard recovered edits"}).click();
  await expect(copy).toHaveCount(0);
  expect(await page.evaluate(() => partialMergeState.source._configDirty)).toBe(false);
  expect(await panel.evaluate(host => host._draft.usage_request_retention_days)).toBe(0);
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  const writes = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
  expect(writes).toHaveLength(1);
  expect(writes[0].config).toEqual({usage_request_retention_days:0});
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).not.toHaveValue("Original retained prompt draft");
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`assistant recovery ignores cleared model ownership from another assistant (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();
  await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"admin", is_admin:true}};});
  await panel.locator("#agent").selectOption("scale-agent-1");
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
  await panel.locator('[data-config="chat_model"]').fill("gpt-4o");
  await panel.locator('[data-config="max_tokens"]').focus();
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocPendingModelPreparation))).toBe(false);
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await panel.locator('[data-config="prompt"]').fill("B retained model draft");
  await panel.evaluate(host => {
    const parent = host.parentElement, hass = host.hass;
    window.retainedModelB = host;
    window.retainedModelBIntent = host._eocModelIntent;
    host.remove();
    localStorage.setItem("extended-openai-agent", "agent-1");
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
  {
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async request => {
        if (request.type.endsWith("/model_catalog") && request.action === "lookup" && request.model === "gpt-5" && !window.oldModelLookupEntered) {
          window.oldModelLookupEntered = true;
          await new Promise(resolve => {window.releaseOldModelLookup = resolve;});
          try {return await original(request);} finally {window.oldModelLookupFinished = true;}
        }
        return original(request);
      };
    });
    await panel.locator('[data-config="chat_model"]').fill("gpt-5");
    await panel.locator('[data-config="max_tokens"]').focus();
    await expect.poll(() => page.evaluate(() => Boolean(oldModelLookupEntered))).toBe(true);

  }
  await panel.locator("#agent").selectOption("scale-agent-1");
  await panel.locator("#confirm-accept").click();
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
  await expect(panel.getByRole("heading", {name:"Recovered unsaved configuration"})).toHaveCount(0);
  expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-4o");
  expect(await panel.evaluate(host => host._draft.prompt)).toBe("B retained model draft");
  expect(await panel.evaluate(host => host._eocReasoningChoiceRevision)).toBe(0);
  expect(await panel.evaluate(host => host._eocModelIntent === retainedModelBIntent)).toBe(true);
  expect(await page.evaluate(() => retainedModelB._configDirty)).toBe(false);
  // Explicitly discard B before returning. A's old lookup must not revive its
  // retired intent or mutate the fresh A draft after this A -> B -> A cycle.
  await panel.locator("#agent").selectOption("agent-1");
  await panel.locator("#confirm-accept").click();
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
  {
    await page.evaluate(() => releaseOldModelLookup());
    await expect.poll(() => page.evaluate(() => Boolean(oldModelLookupFinished))).toBe(true);
  }
  expect(await panel.evaluate(host => host._draft.chat_model)).toBe("gpt-5-mini");
  expect(await panel.evaluate(host => host._eocModelIntent)).toBe(null);
  expect(await panel.evaluate(host => host._eocPendingModelPreparation)).toBe(null);
  expect(await panel.evaluate(host => host._eocReasoningChoiceRevision)).toBe(0);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").length)).toBe(0);
  await expectHarnessClean(page, errors);
});
