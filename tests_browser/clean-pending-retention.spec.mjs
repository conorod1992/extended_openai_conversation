import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function replacePanel(panel) {
  await panel.evaluate(host => {
    window.cleanPendingSources ||= [];
    cleanPendingSources.push(host);
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
}

async function ready(panel, kind) {
  await expect.poll(() => panel.evaluate((host, kind) => !host._busy && (kind === "configuration" ? Boolean(host._configData?.config) : Array.isArray(host._result?.[kind === "memory" ? "memories" : "sources"])), kind)).toBe(true);
}

for (const bundled of [false, true]) test(`clean configuration acknowledgement refreshes the mounted replacement (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await ready(panel, "configuration");
  await panel.evaluate(host => {
    browserHarness.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    host.hass = browserHarness.hass;
  });
  const title = panel.locator('[data-config="__title"]'), tokens = panel.locator('[data-config="max_tokens"]');
  await title.fill("Saved without later edits");
  await tokens.fill("1400");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.cleanConfigurationWrites = [];
    host.hass.callWS = request => request.section === "configuration" && request.action === "save"
      ? new Promise(resolve => cleanConfigurationWrites.push({request, finish:async () => resolve(await original(request))}))
      : original(request);
  });
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => cleanConfigurationWrites.length)).toBe(1);
  await replacePanel(panel);
  await ready(panel, "configuration");
  await expect(title).toHaveValue("Jarvis");
  await expect(tokens).toHaveValue("1200");
  await page.evaluate(() => cleanConfigurationWrites[0].finish());
  await expect(title).toHaveValue("Saved without later edits");
  await expect(tokens).toHaveValue("1400");
  await expect.poll(() => panel.evaluate(host => host._selectedAgent().title)).toBe("Saved without later edits");
  await expect(panel.locator("#agent option:checked")).toHaveText("Saved without later edits");
  expect(await panel.evaluate(host => ({title:host._draftTitle, tokens:host._draft.max_tokens, baselineTitle:host._configData.title, baselineTokens:host._configData.config.max_tokens, dirty:host._configDirty}))).toEqual({title:"Saved without later edits", tokens:1400, baselineTitle:"Saved without later edits", baselineTokens:1400, dirty:false});
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocDraftRecoveryTimer))).toBe(false);
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
  expect(await page.evaluate(() => {
    const event = new Event("beforeunload", {cancelable:true});
    window.dispatchEvent(event);
    return event.defaultPrevented;
  })).toBe(false);
  await replacePanel(panel);
  await ready(panel, "configuration");
  await expect(title).toHaveValue("Saved without later edits");
  await expect(tokens).toHaveValue("1400");
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
  expect(await page.evaluate(() => cleanConfigurationWrites.length)).toBe(1);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`configuration recovery keeps a reverted title and acknowledged token edit (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await ready(panel, "configuration");
  await panel.evaluate(host => {
    browserHarness.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    host.hass = browserHarness.hass;
  });
  const title = panel.locator('[data-config="__title"]'), tokens = panel.locator('[data-config="max_tokens"]');
  const originalTitle = await title.inputValue();
  await title.fill("Acknowledged title A");
  await tokens.fill("1400");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.configurationWrites = [];
    host.hass.callWS = request => request.section === "configuration" && request.action === "save"
      ? new Promise(resolve => configurationWrites.push({request, finish:async () => resolve(await original(request))}))
      : original(request);
  });
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => configurationWrites.length)).toBe(1);
  await title.fill(originalTitle);
  expect(await panel.evaluate(host => host._configDirty)).toBe(true);
  await replacePanel(panel);
  await ready(panel, "configuration");
  expect(await panel.evaluate(host => host._configData.config.max_tokens)).toBe(1200);
  await page.evaluate(() => configurationWrites[0].finish());
  await expect.poll(() => panel.evaluate(host => host._configData.title)).toBe("Acknowledged title A");
  await expect(title).toHaveValue(originalTitle);
  await expect(tokens).toHaveValue("1400");
  expect(await panel.evaluate(host => host._configData.config.max_tokens)).toBe(1400);
  expect(await panel.evaluate(host => host._configDirty)).toBe(true);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => configurationWrites.length)).toBe(2);
  await page.evaluate(() => configurationWrites[1].finish());
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
  expect(await page.evaluate(() => ({title:browserHarness.getState().configuration.title, tokens:browserHarness.getState().configuration.config.max_tokens}))).toEqual({title:originalTitle, tokens:1400});
  await replacePanel(panel);
  await ready(panel, "configuration");
  await expect(title).toHaveValue(originalTitle);
  await expect(tokens).toHaveValue("1400");
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
  expect(await page.evaluate(() => configurationWrites.length)).toBe(2);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) for (const kind of ["knowledge", "memory", "configuration"]) for (const outcome of ["success", "failure", "cancelled"]) {
  test(`clean pending ${kind} intent survives repeated replacement until ${outcome} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const route = kind === "configuration" ? "assistant/basics" : `data-memory/${kind === "memory" ? "memories" : "knowledge"}`;
    await page.goto(fixtureUrl(route, `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel = page.locator("extended-openai-management-panel");
    await ready(panel, kind);
    await panel.evaluate(host => {
      browserHarness.hass = {...host.hass, user:{id:"admin", is_admin:true}};
      host.hass = browserHarness.hass;
    });
    if (kind === "knowledge") {
      await panel.locator("#add-source").click();
      await panel.locator("#knowledge-title").fill("Original valid Knowledge source");
      await panel.locator("#knowledge-content").fill("Original valid content O");
      await panel.locator("#knowledge-save").click();
      await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
      await panel.locator(".source-edit-button").first().click();
      await expect(panel.locator("#knowledge-content")).toBeEnabled();
    } else if (kind === "memory") await panel.locator(".memory-edit-button").first().click();
    const input = panel.locator(kind === "configuration" ? '[data-config="__title"]' : `#${kind}-content`);
    const originalValue = await input.inputValue();
    await input.fill("Submitted version A");
    const section = kind === "memory" ? "memories" : kind, action = kind === "configuration" ? "save" : "update";
    await panel.evaluate((host, {section, action}) => {
      const original = host.hass.callWS.bind(host.hass);
      window.cleanPendingWrites = [];
      host.hass.callWS = request => {
        if (request.section !== section || request.action !== action) return original(request);
        return new Promise((resolve, reject) => cleanPendingWrites.push({request, finish:async outcome => {
          if (outcome === "success") resolve(await original(request));
          else reject(outcome === "cancelled" ? new DOMException("Cancelled pending write", "AbortError") : new Error("Pending write failed"));
        }}));
      };
    }, {section, action});
    await panel.locator(kind === "configuration" ? "#save-config" : `#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => cleanPendingWrites.length)).toBe(1);
    await input.fill(originalValue);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    for (let replacement = 0; replacement < 2; replacement++) {
      await replacePanel(panel);
      await ready(panel, kind);
      expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
      expect(await panel.evaluate(host => host._unsavedState.scopes.get("retained-drafts")?.pending)).toBe(true);
      await panel.locator("#agent").selectOption("scale-agent-1");
      await expect(panel.locator("#agent")).toHaveValue("agent-1");
      await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
    }
    expect(await page.evaluate(() => {
      const event = new Event("beforeunload", {cancelable:true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    })).toBe(true);
    await page.evaluate(outcome => cleanPendingWrites[0].finish(outcome), outcome);
    await expect.poll(() => page.evaluate(() => Boolean(cleanPendingSources[0]._configurationSaving || cleanPendingSources[0]._eocAgentMutations))).toBe(false);
    await expect.poll(() => panel.evaluate(host => Boolean(host._unsavedState.scopes.get("retained-drafts")?.pending))).toBe(false);
    if (outcome === "success") {
      await expect(input).toHaveValue(originalValue);
      if (kind === "configuration") {
        await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(true);
        expect(await panel.evaluate(host => host._configData.title)).toBe("Submitted version A");
      } else await expect.poll(() => panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
      expect(await page.evaluate(kind => kind === "configuration" ? browserHarness.getState().configuration.title : browserHarness.getState()[kind === "memory" ? "memories" : "knowledgeSources"][0].content, kind)).toBe("Submitted version A");
      await panel.locator(kind === "configuration" ? "#save-config" : `#${kind}-save`).click();
      await expect.poll(() => page.evaluate(() => cleanPendingWrites.length)).toBe(2);
      expect(await page.evaluate(() => cleanPendingWrites[1].request.action)).toBe(action);
      await page.evaluate(() => cleanPendingWrites[1].finish("success"));
      await expect.poll(() => panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    } else {
      await expect.poll(() => panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
      expect(await page.evaluate(kind => kind === "configuration" ? browserHarness.getState().configuration.title : browserHarness.getState()[kind === "memory" ? "memories" : "knowledgeSources"][0].content, kind)).toBe(originalValue);
    }
    await expect.poll(() => panel.evaluate(host => Boolean(host._eocDraftRecoveryTimer))).toBe(false);
    expect(await page.evaluate(() => {
      const event = new Event("beforeunload", {cancelable:true});
      window.dispatchEvent(event);
      return event.defaultPrevented;
    })).toBe(false);
    await replacePanel(panel);
    await ready(panel, kind);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    await expect(panel.locator("dialog[open]")).toHaveCount(0);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
    expect(await page.evaluate(() => cleanPendingWrites.length)).toBe(outcome === "success" ? 2 : 1);
    await expectHarnessClean(page, errors);
  });
}
