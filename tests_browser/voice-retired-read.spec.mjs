import {expect, test} from "@playwright/test";
import {fixtureUrl, mountFreshAccountPanel, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false, true]) test(`a retired route read cannot revive consumed Voice configuration; bundle=${bundled}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(async host => {
    host.hass = {...host.hass, user:{id:"voice-retired-read-owner", is_admin:true}};
    const saved = browserHarness.getState().configuration;
    await host.hass.callWS({type:"extended_openai_conversation_responses/management", section:"configuration", action:"save", entry_id:"entry-1", subentry_id:"agent-1", revision:saved.revision,
      config:{...saved.config, voice_scope_policy:"device_mapping", voice_device_mappings:{office:"user:owner"}}});
    const send = host.hass.callWS.bind(host.hass);
    window.retiredVoiceRead = {source:host, generation:1, writes:0, guestWrites:0};
    host.hass.callWS = async request => {
      if (request.section === "guest_mode" && request.action === "get" && retiredVoiceRead.guestUncertain) throw Error("Controlled Guest recovery read failure");
      if (request.type === "extended_openai_conversation_responses/model_catalog" && request.action === "status") return {catalog_generation:retiredVoiceRead.generation, catalog_version:1};
      if (request.type === "config/entity_registry/list") return [{entity_id:"assist_satellite.office", device_id:"office"}];
      const result = await send(request);
      if (request.section === "guest_mode" && request.action === "save_policy" && ++retiredVoiceRead.guestWrites === 1) {retiredVoiceRead.guestUncertain = true; return {};}
      if (request.section === "configuration" && request.action === "save") retiredVoiceRead.writes++;
      if (request.section === "configuration" && request.action === "get") {
        const snapshot = {...result, catalog_generation:retiredVoiceRead.generation, catalog_version:1};
        if (retiredVoiceRead.hold) {
          retiredVoiceRead.hold = false;
          retiredVoiceRead.entered = true;
          await new Promise(resolve => {retiredVoiceRead.release = resolve;});
        }
        return snapshot;
      }
      return result;
    };
    host._invalidateAfterMutation(host._agentId, "configuration", "save");
    host._clearConfigDraft();
    await host._navigate("capabilities", "guest-mode");
  });
  const mode = panel.locator('[data-guest-mode="guest_function_policy"]');
  await expect(mode).toBeVisible();
  const policy = await mode.inputValue() === "off" ? "on" : "off";
  await mode.selectOption(policy);
  await panel.locator("#save-page").click();
  await expect.poll(() => panel.evaluate(host => Boolean(host._unsavedState.scopes.get("capabilities/guest-mode")?.pending))).toBe(false);
  // Discard the uncertain policy's authoring through normal navigation. Its
  // authoritative-read obligation remains, keeping that source recoverable.
  await panel.evaluate(host => {retiredVoiceRead.navigation = host._navigate("assistant", "prompt-context");});
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await page.evaluate(() => retiredVoiceRead.navigation);
  await panel.locator('[data-config="prompt"]').fill("Transferred source prompt");
  await panel.evaluate(host => host._navigate("assistant", "voice"));
  await expect(panel.locator("[data-voice-mapping-row][data-voice-bound]")).toHaveCount(1);
  await panel.locator(".voice-owner-type").selectOption("unretained");
  await panel.evaluate(host => {
    // A normal catalogue validation invalidates the cached configuration and
    // the production route loader starts its read. Hold only that transport reply.
    retiredVoiceRead.generation = 2;
    retiredVoiceRead.hold = true;
    retiredVoiceRead.read = host._loadSection(true);
  });
  await expect.poll(() => page.evaluate(() => retiredVoiceRead.entered)).toBe(true);
  await mountFreshAccountPanel(panel);
  await expect(panel.locator(".voice-mapping-owner")).toHaveValue("unretained");
  expect(await page.evaluate(() => retiredVoiceRead.source._configDirty)).toBe(false);
  await panel.locator(".voice-owner-type").selectOption("shared");
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await panel.locator('[data-config="prompt"]').fill("New saved destination prompt");
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving || host._configDirty))).toBe(false);
  await page.evaluate(async () => {retiredVoiceRead.release(); await retiredVoiceRead.read;});
  await panel.evaluate(host => host._navigate("assistant", "voice"));
  await expect(panel.locator(".voice-mapping-owner")).toHaveValue("shared:household");
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("New saved destination prompt");
  const result = await page.evaluate(() => ({saved:browserHarness.getState().configuration.config, writes:retiredVoiceRead.writes,
    retainedRead:Boolean(retiredVoiceRead.source._unsavedState.scopes.get("capabilities/guest-mode")?.acknowledgedSave?.owns()), sourceDirty:retiredVoiceRead.source._configDirty}));
  expect(result.saved.prompt).toBe("New saved destination prompt");
  expect(result.saved.voice_device_mappings).toEqual({office:"shared:household"});
  expect(result.writes).toBe(1);
  expect(result.retainedRead).toBe(true);
  expect(result.sourceDirty).toBe(false);
  await page.evaluate(() => {retiredVoiceRead.guestUncertain = false;});
  await panel.evaluate(host => host._navigate("capabilities", "guest-mode"));
  await expect(mode).toHaveValue(policy);
  const continuedPolicy = policy === "off" ? "on" : "off";
  await mode.selectOption(continuedPolicy);
  await panel.locator("#save-page").click();
  await expect(panel.locator("#save-page")).toHaveCount(0);
  expect(await page.evaluate(() => browserHarness.getState().guest.config.guest_function_policy)).toBe(continuedPolicy);
  expect(await page.evaluate(() => retiredVoiceRead.guestWrites)).toBe(2);
  expect(await page.evaluate(() => retiredVoiceRead.writes)).toBe(1);
  await expectHarnessClean(page, errors);
});
