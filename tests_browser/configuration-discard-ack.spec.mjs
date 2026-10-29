import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function edit(control, value) {
  if (await control.evaluate(element => element.tagName === "SELECT")) await control.selectOption(String(value));
  else await control.fill(String(value));
}

async function prepare(page, bundled, retention, newerCommit = false) {
  await page.goto(fixtureUrl(retention ? "usage-maintenance/retention" : "assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  const first = panel.locator(retention ? '[data-config="usage_request_retention_days"]' : '[data-config="__title"]');
  const second = panel.locator(retention ? '[data-config="usage_run_retention_days"]' : '[data-config="max_tokens"]');
  await expect(first).toBeVisible();
  await panel.evaluate(host => {
    browserHarness.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    host.hass = browserHarness.hass;
  });
  const original = await first.inputValue(), acknowledged = retention ? "7" : "Acknowledged title A";
  await edit(first, acknowledged);
  await panel.evaluate((host, newerCommit) => {
    const original = host.hass.callWS.bind(host.hass);
    const state = {writes:[], entered:false, failedReads:0, original};
    window.discardAcknowledgement = state;
    host.hass.callWS = async request => {
      if (request.section === "configuration" && ["get", "retention_get"].includes(request.action) && state.failNextRead) {
        state.failNextRead = false;
        state.failedReads++;
        throw new Error("Generated post-save refresh failure");
      }
      if (request.section === "configuration" && request.action === "save") {
        state.writes.push(structuredClone(request));
        if (state.writes.length === 1) {
          const saved = newerCommit ? await original(request) : null;
          state.entered = true;
          return new Promise(resolve => {state.release = async () => resolve(newerCommit ? saved : await original(request));});
        }
      }
      return original(request);
    };
  }, newerCommit);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => discardAcknowledgement.entered)).toBe(true);
  await edit(first, original);
  if (newerCommit) await panel.evaluate(async host => {
    const state = browserHarness.getState().configuration, agent = host._selectedAgent();
    await discardAcknowledgement.original({type:"extended_openai_conversation_responses/management",
      section:"configuration", action:"save", entry_id:agent.entry_id, subentry_id:agent.subentry_id,
      revision:state.revision, title:"Newer committed title C", config:{max_tokens:1550, temperature:0.6}});
  });
  await panel.evaluate(host => {host._invalidateCleanConfiguration(host._agentId); return host._loadSection(true);});
  await page.evaluate(() => {discardAcknowledgement.failNextRead = true; return discardAcknowledgement.release();});
  await expect.poll(() => page.evaluate(() => discardAcknowledgement.failedReads)).toBe(1);
  await expect.poll(() => panel.evaluate(host => host._configurationSaving)).toBe(false);
  expect(await panel.evaluate(host => host._configDataStale)).toBe(true);
  expect(await panel.evaluate(host => Boolean(host._eocAcknowledgedConfiguration))).toBe(true);
  return {panel, first, second, original, acknowledged};
}

async function replacePanel(panel) {
  await panel.evaluate(host => {
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
  await expect.poll(() => panel.evaluate(host => !host._busy && Boolean(host._configData?.config))).toBe(true);
}

for (const bundled of [false, true]) for (const retention of [false, true]) for (const recreate of [false, true]) {
  test(`${retention ? "Retention" : "Assistant"} Discard retires authored changes after failed ACK refresh, replacement=${recreate} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    await discardCase(page, {bundled, retention, recreate});
  });
}

for (const bundled of [false, true]) test(`Discard keeps a newer backend commit before the next save (${bundled ? "bundle" : "source"})`, async ({page}) => {
  await discardCase(page, {bundled, retention:false, recreate:false, newerCommit:true});
});

async function discardCase(page, {bundled, retention, recreate, newerCommit = false}) {
  const errors = trackPageErrors(page);
  const {panel, first, second, acknowledged} = await prepare(page, bundled, retention, newerCommit);
  // Retention uses the ACK-inserted bar without another input binding pass.
  // Assistant's extra normal edit also proves discarding an unrelated field.
  if (!retention) await edit(second, "1300");
  await panel.locator("#revert-config").click();
  await expect(first).toHaveValue(acknowledged);
  await expect(second).toHaveValue(retention ? "30" : newerCommit ? "1550" : "1200");
  expect(await panel.evaluate(host => host._configDirty)).toBe(false);
  expect(await panel.evaluate(host => Boolean(host._eocAcknowledgedConfiguration))).toBe(true);
  expect(await panel.evaluate(host => host._configDataStale)).toBe(true);
  const latestTitle = newerCommit ? "Newer committed title C" : "Acknowledged title A";
  if (recreate) {
    await replacePanel(panel);
    await expect(first).toHaveValue(retention ? acknowledged : latestTitle);
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    await expect.poll(() => panel.evaluate(host => Boolean(host._eocDraftRecoveryTimer))).toBe(false);
  }
  await edit(second, retention ? "7" : "1500");
  await panel.locator("#save-config").click();
  if (!recreate) {
    // The first click certifies the current revision. Discarded O must not
    // return during that read, and no repeated write is dispatched by refresh.
    await expect.poll(() => panel.evaluate(host => host._configDataStale)).toBe(false);
    expect(await page.evaluate(() => discardAcknowledgement.writes.length)).toBe(1);
    await expect(first).toHaveValue(retention ? acknowledged : latestTitle);
    await panel.locator("#save-config").click();
  }
  await expect.poll(() => page.evaluate(() => discardAcknowledgement.writes.length)).toBe(2);
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  const write = await page.evaluate(() => discardAcknowledgement.writes[1]);
  expect(write.config).toEqual(retention ? {usage_run_retention_days:7} : {max_tokens:1500});
  expect(Object.hasOwn(write, "title")).toBe(false);
  const saved = await page.evaluate(() => browserHarness.getState().configuration);
  if (retention) expect(saved.config).toMatchObject({usage_request_retention_days:7, usage_run_retention_days:7});
  else {
    expect(saved.title).toBe(latestTitle);
    expect(saved.config.max_tokens).toBe(1500);
    if (newerCommit) expect(saved.config.temperature).toBe(0.6);
  }
  await replacePanel(panel);
  await expect(first).toHaveValue(retention ? acknowledged : latestTitle);
  await expect(second).toHaveValue(retention ? "7" : "1500");
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
  expect(await page.evaluate(() => discardAcknowledgement.writes.length)).toBe(2);
  await expectHarnessClean(page, errors);
}

for (const bundled of [false, true]) test(`navigation Discard preserves current saved fields after failed ACK refresh (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const {panel, first, second, acknowledged} = await prepare(page, bundled, false);
  await panel.locator("#agent").selectOption("scale-agent-1");
  await acceptConfirmation(panel);
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
  await panel.locator("#agent").selectOption("agent-1");
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
  await expect(first).toHaveValue(acknowledged);
  await edit(second, "1500");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => discardAcknowledgement.writes.length)).toBe(2);
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  expect(await page.evaluate(() => discardAcknowledgement.writes[1].config)).toEqual({max_tokens:1500});
  expect(await page.evaluate(() => Object.hasOwn(discardAcknowledgement.writes[1], "title"))).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().configuration.title)).toBe(acknowledged);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) for (const numericLast of [false, true]) for (const command of ["revert", "save"]) test(`numeric capability ${command} restores controls and subsequent saved state (${bundled ? "bundle" : "source"}, numeric last=${numericLast})`, async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="max_tokens"]')).toBeVisible();
  await panel.evaluate(async host => {
    await host._call("configuration", "save", {config:{max_function_calls_per_conversation:10}, revision:host._configData.revision});
    await host._loadSection(true);
  });
  await panel.locator(".general-advanced summary").click();
  const limit=panel.locator('[data-config="max_function_calls_per_conversation"]');
  const before=await limit.inputValue();
  const other=panel.locator('[data-config="max_tokens"]');
  if (numericLast) await other.fill("1301");
  await limit.fill("11");
  if (!numericLast) await other.fill("1301");
  await panel.locator(command === "revert" ? "#revert-config" : "#save-config").click();
  const expected=command === "revert" ? before : "11";
  await expect(panel.locator("#save-config")).toHaveCount(0);
  await expect(limit).toHaveValue(expected);
  expect(await panel.evaluate(host=>host._draft.max_function_calls_per_conversation)).toBe(Number(expected));
  await panel.locator('[data-config="max_tokens"]').fill("1401");
  await panel.locator("#save-config").click();
  await expect(panel.locator("#save-config")).toHaveCount(0);
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.max_function_calls_per_conversation)).toBe(Number(expected));
});
