import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean, acceptConfirmation} from "./browser-helpers.mjs";

async function prepare(page, bundled, dirtyRetention = false) {
  await page.goto(fixtureUrl("assistant/prompt-context", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  const prompt = panel.locator('[data-config="prompt"]');
  await expect(prompt).toBeVisible();
  await panel.evaluate(host => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    window.fullRetentionBaseline = structuredClone(host._configData.config);
    window.retentionRecoveryBackend = host.hass.callWS.bind(host.hass);
    window.retentionRecoverySources = [];
  });
  await prompt.fill("Unsaved full draft across cold retention recovery");
  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  await expect(panel.locator('[data-config="usage_request_retention_days"]')).toBeVisible();
  if (dirtyRetention) await panel.locator('[data-config="usage_request_retention_days"]').selectOption("7");
  return panel;
}

async function replacePanel(panel, agentId) {
  await panel.evaluate((host, agentId) => {
    const parent = host.parentElement, hass = host.hass;
    window.retentionRecoverySources.push(host);
    host.remove();
    if (agentId) localStorage.setItem("extended-openai-agent", agentId);
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  }, agentId);
}

for (const bundled of [false, true]) for (const dirtyRetention of [false, true]) test(`full draft uses a complete current baseline after Retention replacement, local retention=${dirtyRetention} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await prepare(page, bundled, dirtyRetention);
  await panel.evaluate(async host => {
    const current = browserHarness.getState().configuration;
    const saved = await retentionRecoveryBackend({type:"extended_openai_conversation_responses/management",
      entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId, section:"configuration", action:"save",
      revision:current.revision, config:{chat_model:"gpt-4o", temperature:0.8, usage_request_retention_days:90, usage_run_retention_days:7}});
    window.retentionRecoveryRevision = saved.revision;
  });
  await replacePanel(panel);
  await expect.poll(() => panel.evaluate(host => !host._busy && host._draft?.prompt)).toBe("Unsaved full draft across cold retention recovery");
  const recovered = await panel.evaluate(host => ({config:host._draft, baseline:host._configData.config, projection:host._configData.projection}));
  expect(recovered.config).toMatchObject({chat_model:"gpt-4o", temperature:0.8, usage_request_retention_days:dirtyRetention ? 7 : 90, usage_run_retention_days:7});
  expect(recovered.baseline).toMatchObject({chat_model:"gpt-4o", temperature:0.8, usage_request_retention_days:90, usage_run_retention_days:7});
  expect(recovered.config.functions).toEqual(await page.evaluate(() => fullRetentionBaseline.functions));
  expect(recovered.projection).not.toBe("retention");
  expect(await panel.evaluate(host => host._configData.revision)).toBe(await page.evaluate(() => retentionRecoveryRevision));
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  const prompt = panel.locator('[data-config="prompt"]');
  await expect(prompt).toHaveValue("Unsaved full draft across cold retention recovery");
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  const request = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").at(-1));
  expect(request.config).toEqual({prompt:"Unsaved full draft across cold retention recovery", ...(dirtyRetention ? {usage_request_retention_days:7} : {})});
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`Retained configuration reads remain assistant-owned during a switch (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/prompt-context", `&agents=2${bundled ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="prompt"]')).toBeVisible();
  await panel.evaluate(host => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    window.retentionRecoveryBackend = host.hass.callWS.bind(host.hass);
    window.retentionRecoverySources = [];
  });
  await panel.locator("#agent").selectOption("scale-agent-1");
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
  await panel.locator('[data-config="prompt"]').fill("Retained assistant B prompt");
  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  // HA's selected-assistant preference can change while its old panel is
  // disconnected. Both sources remain separately owned by their real agent.
  await replacePanel(panel, "agent-1");
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await panel.locator('[data-config="prompt"]').fill("Retained assistant A prompt");
  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  await panel.evaluate(host => {
    window.retentionRecoveryHeld = false;
    host.hass.callWS = async request => {
      if (request.section === "configuration" && request.action === "get" && request.subentry_id === "agent-1" && !retentionRecoveryHeld) {
        window.retentionRecoveryHeld = true;
        await new Promise(resolve => {window.retentionRecoveryRelease = resolve;});
      }
      return retentionRecoveryBackend(request);
    };
  });
  await replacePanel(panel);
  await expect.poll(() => page.evaluate(() => retentionRecoveryHeld)).toBe(true);
  await panel.locator("#agent").selectOption("scale-agent-1");
  await acceptConfirmation(panel);
  await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1" && host._draft?.prompt)).toBe("Retained assistant B prompt");
  expect(await panel.evaluate(host => host._configData.projection)).not.toBe("retention");
  await page.evaluate(() => retentionRecoveryRelease());
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Retained assistant B prompt");
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  expect(await page.evaluate(() => retentionRecoverySources[1]._configDirty)).toBe(false);
  const writes = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
  expect(writes.map(call => ({agent:call.subentry_id, config:call.config}))).toEqual([
    {agent:"scale-agent-1", config:{prompt:"Retained assistant B prompt"}},
  ]);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) for (const interruption of ["failure", "replacement"]) test(`full Retention recovery retains source through ${interruption} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await prepare(page, bundled);
  await panel.evaluate((host, interruption) => {
    window.retentionRecoveryReads = 0;
    window.retentionRecoveryEntered = false;
    host.hass.callWS = async request => {
      if (request.section === "configuration" && request.action === "get") {
        window.retentionRecoveryReads++;
        if (window.retentionRecoveryReads === 1) {
          window.retentionRecoveryEntered = true;
          if (interruption === "failure") throw new Error("Generated complete-baseline recovery failure");
          await new Promise(resolve => {window.retentionRecoveryRelease = resolve;});
        }
      }
      return retentionRecoveryBackend(request);
    };
  }, interruption);
  await replacePanel(panel);
  await expect.poll(() => page.evaluate(() => retentionRecoveryEntered)).toBe(true);
  expect(await page.evaluate(() => retentionRecoverySources[0]._configDirty)).toBe(true);
  expect(await page.evaluate(() => retentionRecoverySources[0]._draft.chat_model)).toBe(await page.evaluate(() => fullRetentionBaseline.chat_model));
  if (interruption === "failure") {
    await expect(panel.getByText("Unsaved configuration is retained. Refresh this section to retry loading the complete settings.", {exact:true})).toBeVisible();
    // Ordinary renders cannot loop a failing read or consume its source.
    await panel.evaluate(host => {host._render(); host._render();});
    expect(await page.evaluate(() => retentionRecoveryReads)).toBe(1);
    await panel.evaluate(host => host._navigate("guide", "getting-started"));
    await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  } else {
    await replacePanel(panel);
  }
  await expect.poll(() => panel.evaluate(host => host._draft?.prompt)).toBe("Unsaved full draft across cold retention recovery");
  expect(await panel.evaluate(host => host._draft.chat_model)).toBe(await page.evaluate(() => fullRetentionBaseline.chat_model));
  expect(await page.evaluate(() => retentionRecoveryReads)).toBe(2);
  if (interruption === "replacement") {
    await page.evaluate(() => retentionRecoveryRelease());
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await panel.evaluate(host => host._draft.prompt)).toBe("Unsaved full draft across cold retention recovery");
  }
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
  const writes = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
  expect(writes).toHaveLength(1);
  expect(writes[0].config).toEqual({prompt:"Unsaved full draft across cold retention recovery"});
  await expectHarnessClean(page, errors);
});
