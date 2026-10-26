import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function replacePanel(page, user = "admin", agentId, ready = '[data-config="__title"]') {
  await page.evaluate(({user, agentId}) => {
    const old = document.querySelector("extended-openai-management-panel"), parent = old.parentElement;
    const hass = {...old.hass, user:{id:user, is_admin:true}};
    old.remove();
    if (agentId) localStorage.setItem("extended-openai-agent", agentId);
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  }, {user, agentId});
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(ready).first()).toBeVisible();
  await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
}

for (const bundled of [false, true]) {
  test(`configuration acknowledgement expands a cold Retention projection before reconnect handoff (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    const originalConfig = await panel.evaluate(host => structuredClone(host._configData.config));
    await startPendingSave(page, panel, false);
    await replacePanel(page);
    await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
    await expect(panel.locator("[data-retention-config]").first()).toBeVisible();
    await replacePanel(page, "admin", undefined, "[data-retention-config]");
    // Unlike a warm route switch, this newly created Retention panel has only
    // the narrow backend projection and cannot certify the global draft yet.
    expect(await panel.evaluate(host => host._configData.projection)).toBe("retention");
    expect(await panel.evaluate(host => Object.keys(host._configData.config).sort())).toEqual([
      "usage_request_retention_days", "usage_run_retention_days",
    ]);
    await page.evaluate(() => {
      window.reconnectAck.holdRefresh = true;
      window.reconnectAck.releaseAcknowledgement();
    });
    await expect.poll(() => page.evaluate(() => window.reconnectAck.refreshEntered)).toBe(true);
    expect(await panel.evaluate(host => host._configData.projection)).toBe("retention");
    await page.evaluate(() => window.reconnectAck.releaseRefresh());
    await expect.poll(() => panel.evaluate(host => ({title:host._draftTitle, tokens:host._draft?.max_tokens, dirty:host._configDirty})))
      .toEqual({title:"Jarvis", tokens:1400, dirty:true});
    expect(await panel.evaluate(host => host._configData.projection)).not.toBe("retention");
    expect(await panel.evaluate(host => host._draft)).toEqual({...originalConfig, max_tokens:1400});
    const saved = await page.evaluate(() => browserHarness.getState().configuration);
    expect(await panel.evaluate(host => host._configData.revision)).toBe(saved.revision);
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(1);
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
    const writes = await page.evaluate(() => window.reconnectAck.writes);
    expect(writes).toHaveLength(2);
    expect(writes[1]).toMatchObject({revision:saved.revision, title:"Jarvis", config:{}});
    expect(Object.keys(writes[1].config)).toEqual([]);
    expect(await page.evaluate(() => browserHarness.getState().configuration.config)).toEqual({...originalConfig, max_tokens:1400});
    await expectHarnessClean(page, diagnostics);
  });
}

async function startPendingSave(page, panel, afterCommit) {
  await panel.evaluate((host, afterCommit) => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    const original = host.hass.callWS.bind(host.hass);
    const state = {original, source:host, writes:[], entered:false, reads:0, refreshEntered:false, failedReads:0};
    window.reconnectAck = state;
    host.hass.callWS = async request => {
      if (request.section === "configuration" && request.action === "get") {
        state.reads++;
        if (state.holdRefresh) {
          state.holdRefresh = false;
          state.refreshEntered = true;
          await new Promise(resolve => {state.releaseRefresh = resolve;});
          if (state.failRefresh) { state.failedReads++; throw new Error("Generated reconnect refresh failure"); }
        }
      }
      if (request.section === "configuration" && request.action === "save") {
        state.writes.push(structuredClone(request));
        if (state.writes.length === 1) {
          const saved = afterCommit ? await original(request) : null;
          state.entered = true;
          await new Promise(resolve => {state.releaseAcknowledgement = resolve;});
          return afterCommit ? saved : original(request);
        }
      }
      return original(request);
    };
  }, afterCommit);
  await panel.locator('[data-config="__title"]').fill("Submitted title A");
  await panel.locator('[data-config="max_tokens"]').fill("1400");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => window.reconnectAck.entered)).toBe(true);
  await panel.locator('[data-config="__title"]').fill("Jarvis");
}

for (const bundled of [false, true]) for (const afterCommit of [false, true]) for (const failRefresh of [false, true]) {
  test(`configuration acknowledgement recovers reverted edits across two replacements after ${afterCommit ? "newer committed" : "precommit"} read${failRefresh ? " and failed refresh" : ""} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    const originalCollections = await panel.evaluate(host => ({functions:host._configData.config.functions, groups:host._configData.config.function_groups}));
    await startPendingSave(page, panel, afterCommit);
    if (afterCommit) await panel.evaluate(async host => {
      const saved = browserHarness.getState().configuration, agent = host._selectedAgent();
      await window.reconnectAck.original({type:"extended_openai_conversation_responses/management",
        section:"configuration", action:"save", entry_id:agent.entry_id, subentry_id:agent.subentry_id,
        revision:saved.revision, title:"Independent title C", config:{max_tokens:1550, temperature:0.6}});
    });
    // This actual route refresh accepts either O (before A commits) or C.
    await panel.evaluate(host => {
      host._invalidateCleanConfiguration(host._agentId);
      return host._loadSection(true);
    });
    expect(await page.evaluate(() => window.reconnectAck.reads)).toBe(1);
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(1400);
    expect(await panel.evaluate(host => host._configData.title)).toBe(afterCommit ? "Independent title C" : "Jarvis");
    await replacePanel(page);
    await replacePanel(page);
    await page.evaluate(failRefresh => {
      window.reconnectAck.failRefresh = failRefresh;
      window.reconnectAck.holdRefresh = true;
      window.reconnectAck.releaseAcknowledgement();
    }, failRefresh);
    await expect.poll(() => page.evaluate(() => window.reconnectAck.refreshEntered)).toBe(true);
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(1);
    await page.evaluate(() => window.reconnectAck.releaseRefresh());
    await expect.poll(() => page.evaluate(() => window.reconnectAck.source._configurationSaving)).toBe(false);
    if (failRefresh) {
      await expect.poll(() => page.evaluate(() => window.reconnectAck.failedReads)).toBe(1);
      await expect(panel.locator("#toast")).toContainText("configuration could not be refreshed");
      // Failed reads retain the source delta. A normal section retry supplies
      // the authoritative baseline required before ownership can transfer.
      await panel.evaluate(host => host._loadSection(true));
    }
    const expectedTokens = afterCommit ? 1550 : 1400;
    await expect.poll(() => panel.evaluate(host => ({title:host._draftTitle, tokens:host._draft?.max_tokens, dirty:host._configDirty})))
      .toEqual({title:"Jarvis", tokens:expectedTokens, dirty:true});
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    const beforeSave = await page.evaluate(() => browserHarness.getState().configuration);
    expect(beforeSave.title).toBe(afterCommit ? "Independent title C" : "Submitted title A");
    expect(beforeSave.config.max_tokens).toBe(expectedTokens);
    expect(await panel.evaluate(host => host._configData.revision)).toBe(beforeSave.revision);
    if (afterCommit) expect(await panel.evaluate(host => host._draft.temperature)).toBe(0.6);
    expect(await panel.evaluate(host => ({functions:host._draft.functions, groups:host._draft.function_groups}))).toEqual(originalCollections);
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(1);

    // The recovered save uses the latest revision and only the remaining title
    // delta. It must not replay A or overwrite independent C's other fields.
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => window.reconnectAck.writes.length)).toBe(2);
    await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
    const writes = await page.evaluate(() => window.reconnectAck.writes);
    expect(writes[1]).toMatchObject({revision:beforeSave.revision, title:"Jarvis", config:{}});
    expect(Object.keys(writes[1].config)).toEqual([]);
    const persisted = await page.evaluate(() => browserHarness.getState().configuration);
    expect(persisted.title).toBe("Jarvis");
    expect(persisted.config.max_tokens).toBe(expectedTokens);
    if (afterCommit) expect(persisted.config.temperature).toBe(0.6);
    await replacePanel(page);
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(2);
    await expectHarnessClean(page, diagnostics);
  });
}

for (const bundled of [false, true]) for (const differentUser of [false, true]) {
  test(`configuration acknowledgement cannot transfer to ${differentUser ? "another user" : "another assistant"} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    await startPendingSave(page, panel, true);
    await replacePanel(page, differentUser ? "other-admin" : "admin", differentUser ? undefined : "scale-agent-1");
    if (!differentUser) {
      await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("scale-agent-1");
    }
    await page.evaluate(() => window.reconnectAck.releaseAcknowledgement());
    await expect.poll(() => page.evaluate(() => window.reconnectAck.source._configurationSaving)).toBe(false);
    // A completed normal route read gives recovery its ordinary retry point.
    await panel.evaluate(host => host._loadSection(true));
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await panel.evaluate(host => host._draftTitle)).toBe("Submitted title A");
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(1);
    if (differentUser) await replacePanel(page, "admin");
    else {
      await panel.locator("#agent").selectOption("agent-1");
      await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("agent-1");
    }
    await expect.poll(() => panel.evaluate(host => ({title:host._draftTitle, dirty:host._configDirty})))
      .toEqual({title:"Jarvis", dirty:true});
    await panel.locator("#revert-config").click();
    await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await page.evaluate(() => window.reconnectAck.writes.length)).toBe(1);
    await expectHarnessClean(page, diagnostics);
  });
}
