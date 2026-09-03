import {bindVoiceIdentity, transformVoiceIdentity} from "./voice-identity-ui.js";

const PATCHED = Symbol.for("extended-openai.management-voice-identity");

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
