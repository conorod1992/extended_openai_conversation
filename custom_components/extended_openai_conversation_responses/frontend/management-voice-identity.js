import {getRouteFeature} from "./management-route.js";

const PATCHED = Symbol.for("extended-openai.management-voice-identity");

export function installManagementVoiceIdentity(Panel) {
  // A constructor is the production API; registry callers remain supported.
  if (typeof Panel !== "function") {
    const registry = Panel || globalThis.customElements;
    if (!registry?.whenDefined) return Promise.resolve(false);
    return registry.whenDefined("extended-openai-management-panel").then(() => installManagementVoiceIdentity(registry.get("extended-openai-management-panel")));
  }
  const constructor = Panel;
  const prototype = constructor?.prototype;
  if (!prototype || prototype[PATCHED]) return false;

  const originalContent = prototype._content;
  prototype._content = function(agent) {
    const content = originalContent.call(this,agent);
    if (this._viewKey() !== "assistant/voice") return content;
    return getRouteFeature("assistant/voice")?.transformVoiceIdentity(this,content) || this._loading();
  };

  const originalBindActions = prototype._bindActions;
  prototype._bindActions = function(...args) {
    const result = originalBindActions.apply(this,args);
    if (this._viewKey() === "assistant/voice") getRouteFeature("assistant/voice")?.bindVoiceIdentity(this);
    return result;
  };

  prototype[PATCHED] = true;
  return true;
}
