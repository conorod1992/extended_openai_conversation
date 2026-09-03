import {NAVIGATION, SETTINGS_INDEX} from "./frontend-navigation.js";
import {bindVoiceIdentity, transformVoiceIdentity} from "./voice-identity-ui.js";

const PATCHED = Symbol.for("extended-openai.management-voice-identity");

function installVoiceMetadata() {
  const voice = NAVIGATION.find((item) => item.id === "assistant")?.sections?.find((item) => item.id === "voice");
  if (voice) {
    voice.label = "Voice & identity";
    voice.description = "Choose identity and retained-data behavior for signed-in and unidentified voice requests.";
  }
  const updates = {
    voice_scope_policy: ["Unidentified voice requests", "Choose which retained-data scope is used when Home Assistant does not identify the speaker.", "voice unidentified speaker scope household user"],
    voice_unmapped_policy: ["Unmapped-device fallback", "Choose the retained-data scope used when device assignment is active but the source device has no assignment.", "voice unmapped satellite device fallback scope"],
    voice_default_user_id: ["Default voice user", "Home Assistant user whose retained data is used when a voice policy selects the default user.", "voice default user identity home assistant"],
    voice_device_mappings: ["Voice device assignments", "Assign source voice devices to users, shared household data, or no retained personal data.", "voice mappings satellite devices assignments users household"],
  };
  for (const item of SETTINGS_INDEX) {
    const update = updates[item.configKey];
    if (!update) continue;
    [item.label,item.description,item.terms] = update;
  }
}

installVoiceMetadata();

export function installManagementVoiceIdentity(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalContent = prototype._content;
    prototype._content = function(agent) {
      const content = originalContent.call(this,agent);
      if (this._viewKey() !== "assistant/voice") return content;
      return transformVoiceIdentity(this,content);
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this,args);
      if (this._viewKey() === "assistant/voice") bindVoiceIdentity(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementVoiceIdentity();
}

export {installVoiceMetadata};
