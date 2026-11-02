import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

async function setup(page) {
  await page.goto(fixtureUrl("assistant/basics"));
  await page.waitForFunction(() => browserHarness?.panel?._configData?.revision);
  await page.evaluate(async () => {
    const h = browserHarness, p = h.panel, saved = h.getState().configuration;
    h.hass.user = {id:"voice-admin"};
    await h.hass.callWS({type:"extended_openai_conversation_responses/management",
      section:"configuration", action:"save", entry_id:"entry-1", subentry_id:"agent-1",
      revision:saved.revision, config:{...saved.config, voice_scope_policy:"device_mapping",
        voice_device_mappings:{office:"user:owner", living:"shared:household"}}});
    const send = h.hass.callWS.bind(h.hass);
    h.hass.callWS = async message => {
      if (message.type === "config/entity_registry/list") return ["office", "living"]
        .map(id => ({entity_id:`assist_satellite.${id}`, device_id:id}));
      if (message.type === "config/entity_registry/get") {
        if (h.reviewVoicePending) return new Promise(resolve => {h.reviewVoiceRelease = resolve;});
        return {entity_id:message.entity_id, device_id:message.entity_id.split(".")[1]};
      }
      return send(message);
    };
    p._invalidateAfterMutation(p._agentId, "configuration", "save");
    p._clearConfigDraft();
    await p._navigate("assistant", "voice");
  });
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await expect(panel.locator(".voice-mapping-owner").nth(0)).toHaveValue("user:owner");
  return panel;
}

async function refreshWithNewMappings(page) {
  await page.evaluate(async () => {
    const h = browserHarness, p = h.panel, saved = h.getState().configuration;
    await h.hass.callWS({type:"extended_openai_conversation_responses/management",
      section:"configuration", action:"save", entry_id:"entry-1", subentry_id:"agent-1",
      revision:saved.revision, config:{voice_device_mappings:{office:"shared:household", living:"user:owner"}}});
    p._invalidateAfterMutation(p._agentId, "configuration", "save");
    await p._loadConfigDraft({full:true});
    p._render();
  });
}

test("fresh configuration read updates clean hydrated Voice assignments", async ({page}) => {
  const panel = await setup(page);
  await refreshWithNewMappings(page);
  expect(await panel.evaluate(p => p._draft.voice_device_mappings)).toEqual({office:"shared:household", living:"user:owner"});
  await expect(panel.locator(".voice-mapping-owner").nth(0)).toHaveValue("shared:household");
  await expect(panel.locator(".voice-mapping-owner").nth(1)).toHaveValue("user:owner");
  await panel.evaluate(async p => {
    const {readConfigurationDraft} = await import("/custom_components/extended_openai_conversation_responses/frontend/configuration-controls.js");
    readConfigurationDraft(p);
    p._draft.prompt = "Unrelated prompt edit after refreshed Voice data";
    p._syncConfigDirty();
    p._render();
  });
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.voice_device_mappings))
    .toEqual({office:"shared:household", living:"user:owner"});
  await panel.locator(".voice-owner-type").nth(0).selectOption("unretained");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.voice_device_mappings))
    .toEqual({office:"unretained", living:"user:owner"});
});

for (const edit of ["valid", "duplicate", "pending"]) {
  test(`fresh configuration read preserves newer ${edit} Voice row edits`, async ({page}) => {
    const panel = await setup(page);
    if (edit === "valid") await panel.locator(".voice-owner-type").nth(0).selectOption("unretained");
    else {
      if (edit === "pending") await page.evaluate(() => {browserHarness.reviewVoicePending = true;});
      await panel.locator(".voice-satellite-picker").nth(0).evaluate(picker => {
        picker.value = "assist_satellite.living";
        picker.dispatchEvent(new CustomEvent("value-changed", {detail:{value:picker.value}, bubbles:true, composed:true}));
      });
      await expect(panel.locator('[data-error="voice_device_mappings"]'))
        .toContainText(edit === "pending" ? "Wait" : "more than once");
    }
    await refreshWithNewMappings(page);
    expect(await panel.evaluate(p => p._configDirty)).toBe(true);
    if (edit === "valid") {
      await expect(panel.locator(".voice-mapping-owner").nth(0)).toHaveValue("unretained");
      expect(await panel.evaluate(p => p._draft.voice_device_mappings))
        .toEqual({office:"unretained", living:"shared:household"});
    } else {
      await expect(panel.locator('[data-error="voice_device_mappings"]'))
        .toContainText(edit === "pending" ? "Wait" : "more than once");
      if (edit === "duplicate") await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("living");
      if (edit === "pending") {
        await page.evaluate(() => browserHarness.reviewVoiceRelease({entity_id:"assist_satellite.living", device_id:"living"}));
        await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
      }
    }
    await panel.locator("#revert-config").click();
    await expect(panel.locator(".voice-mapping-owner").nth(0)).toHaveValue("shared:household");
    await expect(panel.locator(".voice-mapping-owner").nth(1)).toHaveValue("user:owner");
    expect(await panel.evaluate(p => p._configDirty)).toBe(false);
  });
}
