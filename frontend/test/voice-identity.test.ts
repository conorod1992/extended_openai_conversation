// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";

import {NAVIGATION, searchSettings} from "../../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {installVoiceMetadata} from "../../custom_components/extended_openai_conversation_responses/frontend/management-voice-identity.js";
import {renderVoiceIdentity, voiceIdentitySummary, voiceUserLabel, voiceUsers} from "../../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js";

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
      voice_device_mappings:{kitchen:"user:user-1",hall:"shared"},
    },
  },
};

describe("Voice & identity management UX", () => {
  it("uses the existing Home Assistant scope catalogue for friendly user choices", () => {
    expect(voiceUsers(panel)).toEqual([{id:"user-1",name:"Conor"},{id:"user-2",name:"Alex"}]);
    expect(voiceUserLabel(panel,"user:user-1")).toBe("Conor");
    expect(voiceUserLabel(panel,"missing-user")).toBeNull();
  });

  it("renders guided identity controls instead of raw IDs and JSON editing", () => {
    const html = renderVoiceIdentity(panel);
    expect(html).toContain("Signed-in identity wins");
    expect(html).toContain("No identity guessing");
    expect(html).toContain('id="config-voice_default_user_id"');
    expect(html).toContain('<option value="user-1" selected>Conor</option>');
    expect(html).toContain('id="voice-mappings"');
    expect(html).toContain('value="kitchen"');
    expect(html).toContain('value="user:user-1" selected>Conor</option>');
    expect(html).not.toContain("Voice device assignments (JSON)");
    expect(html).not.toContain("Default Home Assistant user ID");
    expect(html).not.toContain("<textarea");
  });

  it("explains the effective unidentified-voice path", () => {
    expect(voiceIdentitySummary(panel._result.config,voiceUsers(panel))).toBe(
      "Unidentified voice requests use 2 saved device assignments. Devices without an assignment use the default user (Conor).",
    );
    expect(voiceIdentitySummary({...panel._result.config,voice_scope_policy:"default_user",voice_default_user_id:""},voiceUsers(panel))).toContain(
      "none is selected — so no personal data is retained",
    );
  });

  it("updates navigation and settings-search terminology without changing config keys", () => {
    installVoiceMetadata();
    const voice = NAVIGATION.find((item) => item.id === "assistant")?.sections.find((item) => item.id === "voice");
    expect(voice?.label).toBe("Voice & identity");
    expect(searchSettings("default voice user")[0]?.configKey).toBe("voice_default_user_id");
    expect(searchSettings("voice device assignments")[0]?.target).toBe("voice-mappings");
    expect(searchSettings("unmapped-device fallback")[0]?.configKey).toBe("voice_unmapped_policy");
  });

  it("loads as a bootstrap extension before persistent rendering", async () => {
    const bootstrap = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js",import.meta.url),"utf8");
    expect(bootstrap).toContain('"./management-voice-identity.js"');
    expect(bootstrap.indexOf("management-voice-identity.js")).toBeLessThan(bootstrap.indexOf("management-rendering-performance.js"));
  });
});
