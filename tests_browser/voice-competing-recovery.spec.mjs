import {expect, test} from "@playwright/test";
import {fixtureUrl, mountFreshAccountPanel, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const decision of ["keep", "discard"]) test(`competing Voice recovery preserves a later saved destination: ${decision}, bundle=${bundled}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(async host => {
    host.hass = {...host.hass, user:{id:"voice-competing-admin", is_admin:true}};
    const saved = browserHarness.getState().configuration;
    await host.hass.callWS({type:"extended_openai_conversation_responses/management", section:"configuration", action:"save", entry_id:"entry-1", subentry_id:"agent-1", revision:saved.revision, title:saved.title, config:{...saved.config, voice_scope_policy:"device_mapping", voice_device_mappings:{office:"user:owner", living:"shared:household"}}});
    const send = host.hass.callWS.bind(host.hass);
    window.voiceCompetition = {writes:0, source:host};
    host.hass.callWS = async request => {
      if (request.type === "config/entity_registry/list") return ["office", "living"].map(id => ({entity_id:`assist_satellite.${id}`, device_id:id}));
      if (request.type === "config/entity_registry/get") return {entity_id:request.entity_id, device_id:request.entity_id.split(".")[1]};
      const result = await send(request);
      if (request.section === "configuration" && request.action === "save") {
        voiceCompetition.writes++;
        if (voiceCompetition.writes === 1) {
          voiceCompetition.committed = true;
          await new Promise(resolve => {voiceCompetition.release = resolve;});
        }
      }
      return result;
    };
    host._invalidateAfterMutation(host._agentId, "configuration", "save");
    host._clearConfigDraft();
    await host._navigate("assistant", "prompt-context");
  });
  await panel.locator('[data-config="prompt"]').fill("Submitted before competing Voice edits");
  await panel.evaluate(host => host._navigate("assistant", "voice"));
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => voiceCompetition.committed)).toBe(true);
  const mappingReady = async () => {
    await expect(panel.locator("[data-voice-mapping-feature][data-ready]")).toBeAttached();
    await expect(panel.locator("[data-voice-mapping-row][data-voice-bound]")).toHaveCount(2);
  };
  const chooseSatellite = async (index, value) => {
    // Readiness, picker resolution and dispatch must share one browser task:
    // a remount between separate checks can retire the previously ready rows.
    // Retry only while no mounted handler has accepted the selection.
    await expect.poll(() => panel.evaluate((host, {index, selected}) => {
      const root = host.shadowRoot;
      const rows = root.querySelectorAll("[data-voice-mapping-row][data-voice-bound]");
      if (!root.querySelector("[data-voice-mapping-feature][data-ready]") || rows.length !== 2) return false;
      const picker = rows[index]?.querySelector(".voice-satellite-picker");
      if (!picker?.isConnected) return false;
      const prior = picker.__voiceSelection;
      picker.value = selected;
      picker.dispatchEvent(new CustomEvent("value-changed", {detail:{value:selected}, bubbles:true, composed:true}));
      return picker.__voiceSelection !== prior;
    }, {index, selected:value})).toBe(true);
  };
  await chooseSatellite(0, "assist_satellite.living");
  await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("shared");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
  await mountFreshAccountPanel(panel);
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await chooseSatellite(1, "assist_satellite.office");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
  await page.evaluate(() => voiceCompetition.release());
  const review = panel.locator("dialog[data-eoc-recovered-editor][open]");
  await expect(review).toBeVisible();
  await expect(review.locator("h2")).toHaveText("Recovered Voice assignments");
  const copies = JSON.parse(await review.locator("textarea").inputValue());
  expect(copies.recovered.map(row => row.deviceId)).toEqual(["living", "living"]);
  expect(copies.current.map(row => row.deviceId)).toEqual(["office", "office"]);
  await panel.evaluate(host => host._render());
  await mappingReady();
  await review.getByRole("button", {name:decision === "keep" ? "Keep draft for later" : "Discard recovered edits", exact:true}).click();
  await chooseSatellite(1, "assist_satellite.living");
  await expect(panel.locator(".voice-device-id").nth(1)).toHaveValue("living");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toBeEmpty();
  await panel.locator(".voice-owner-type").nth(0).selectOption("unretained");
  await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("unretained");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toBeEmpty();
  await expect(review).toHaveCount(0);
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => Boolean(host._configurationSaving || host._configDirty))).toBe(false);
  const expected = {office:"unretained", living:"shared:household"};
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.voice_device_mappings)).toEqual(expected);
  for (let visit = 0; visit < 2; visit++) {
    await mountFreshAccountPanel(panel);
    await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
    if (decision === "keep") {
      await expect(review).toBeVisible();
      const retained = JSON.parse(await review.locator("textarea").inputValue());
      expect(retained.recovered).toEqual(copies.recovered);
      expect(retained.current.map(row => row.deviceId)).toEqual(["office", "living"]);
      await review.getByRole("button", {name:visit === 0 ? "Keep draft for later" : "Discard recovered edits", exact:true}).click();
    } else await expect(review).toHaveCount(0);
    expect(await panel.evaluate(host => host._draft.voice_device_mappings)).toEqual(expected);
    await expect(panel.locator('[data-config="voice_unmapped_policy"]')).toHaveValue("unretained");
    await expect(review).toHaveCount(0);
    await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("office");
    await expect(panel.locator(".voice-owner-type").nth(0)).toHaveValue("unretained");
  }
  expect(await page.evaluate(() => voiceCompetition.writes)).toBe(2);
  await expectHarnessClean(page, errors);
});
