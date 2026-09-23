// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";

import {NAVIGATION} from "../../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {searchSettings} from "../../custom_components/extended_openai_conversation_responses/frontend/management-settings-index.js";
import {
  deviceIdForSatellite,
  renderVoiceIdentity,
  satelliteForDeviceId,
  voiceIdentitySummary,
  voiceUserLabel,
  voiceUsers,
} from "../../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js";
import {renderVoiceIdentityCore} from "../../custom_components/extended_openai_conversation_responses/frontend/voice-identity-core.js";

const escape = (value) => String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
const panel = {
  _e: escape,
  _baseScopes: [
    {scope_id:"user:user-1",scope_type:"user",display_name:"Conor"},
    {scope_id:"user:user-2",scope_type:"user",display_name:"Alex"},
    {scope_id:"shared:household",scope_type:"shared",display_name:"Shared household"},
  ],
  _result: {
    options: {
      voice_scope_policy: ["unretained","shared","default_user","device_mapping"].map((value) => ({value,label:value})),
      voice_unmapped_policy: ["unretained","shared","default_user","device_mapping"].map((value) => ({value,label:value})),
    },
    config: {
      voice_scope_policy:"device_mapping",
      voice_unmapped_policy:"default_user",
      voice_default_user_id:"user-1",
      voice_device_mappings:{"device-kitchen":"user:user-1","device-hall":"shared"},
    },
  },
};

describe("Voice & identity management UX", () => {
  it("retains the scope catalogue only as a compatibility label fallback", () => {
    expect(voiceUsers(panel)).toEqual([{id:"user-1",name:"Conor"},{id:"user-2",name:"Alex"}]);
    expect(voiceUserLabel(panel,"user:user-1")).toBe("Conor");
    expect(voiceUserLabel(panel,"missing-user")).toBeNull();
  });

  it("renders Home Assistant native user and Assist satellite pickers without exposing device IDs", () => {
    const html = renderVoiceIdentity(panel);
    expect(html).toContain("Signed-in identity wins");
    expect(html).toContain("No identity guessing");
    expect(html).toContain('id="config-voice_default_user_picker"');
    expect(html).toContain("<ha-user-picker");
    expect(html).toContain("<ha-entity-picker");
    expect(html).toContain('class="voice-native-picker voice-satellite-picker"');
    expect(html).toContain('value="device-kitchen"');
    expect(html).toContain('value="user:user-1"');
    expect(html).toContain("Home Assistant user");
    expect(html).toContain("Shared household");
    expect(html).toContain("No retained personal data");
    expect(html).not.toContain("Voice device assignments (JSON)");
    expect(html).not.toContain("Default Home Assistant user ID");
    expect(html).not.toContain("Device ID<input");
    expect(html).not.toContain("<textarea");
  });

  it("renders policy controls and a mapping placeholder without mapping picker code", () => {
    const html = renderVoiceIdentityCore(panel);
    expect(html).toContain('data-config="voice_scope_policy"');
    expect(html).toContain('id="config-voice_default_user_picker"');
    expect(html).toContain("Loading saved assignments");
    expect(html).not.toContain("<ha-entity-picker");
    expect(html).not.toContain('value="device-kitchen"');
    expect(panel._result.config.voice_device_mappings).toEqual({"device-kitchen":"user:user-1","device-hall":"shared"});
    expect(renderVoiceIdentityCore({...panel, _draft:{...panel._result.config, voice_scope_policy:"shared"}})).not.toContain("Loading saved assignments");
  });

  it("translates Assist satellite entity IDs to the existing stored device IDs", () => {
    const registry = [
      {entity_id:"assist_satellite.kitchen",device_id:"device-kitchen"},
      {entity_id:"assist_satellite.hall",device_id:"device-hall"},
      {entity_id:"sensor.kitchen_temperature",device_id:"device-kitchen"},
    ];
    expect(deviceIdForSatellite(registry,"assist_satellite.kitchen")).toBe("device-kitchen");
    expect(satelliteForDeviceId(registry,"device-kitchen")).toBe("assist_satellite.kitchen");
    expect(satelliteForDeviceId(registry,"missing-device")).toBe("");
  });

  it("explains the effective unidentified-voice path", () => {
    expect(voiceIdentitySummary(panel._result.config,voiceUsers(panel))).toBe(
      "Unidentified voice requests use 2 saved device assignments. Devices without an assignment use the default user (Conor).",
    );
    expect(voiceIdentitySummary({...panel._result.config,voice_scope_policy:"default_user",voice_default_user_id:""},voiceUsers(panel))).toContain(
      "none is selected — so no personal data is retained",
    );
  });

  it("keeps final navigation and settings-search terminology in the canonical metadata", () => {
    const voice = NAVIGATION.find((item) => item.id === "assistant")?.sections.find((item) => item.id === "voice");
    expect(voice?.label).toBe("Voice & identity");
    expect(searchSettings("default voice user")[0]?.configKey).toBe("voice_default_user_id");
    expect(searchSettings("voice device assignments")[0]?.target).toBe("voice-mappings");
    expect(searchSettings("unmapped-device fallback")[0]?.configKey).toBe("voice_unmapped_policy");
  });

  it("loads route-owned Voice Identity UI lazily", async () => {
    const route = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-route.js",import.meta.url),"utf8");
    expect(route).toContain('"assistant/voice": () => import("./voice-identity-core.js")');
    const core = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/voice-identity-core.js",import.meta.url),"utf8");
    expect(core).toContain('await import("./voice-identity-ui.js")');
    expect(route).not.toContain('"assistant/prompt-context": () => import("./exposed-attributes-ui.js")');
  });
});
