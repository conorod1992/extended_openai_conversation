const PATCHED = Symbol.for("extended-openai.management-conversation-default-label");

const HOME_ASSISTANT_SESSION_DEFAULT = "Default: Use Home Assistant sessions";

export function normalizeConversationContinuityDefault(panel) {
  const field = panel?.shadowRoot?.querySelector?.('[data-field="conversation_continuity"]');
  if (!field) return false;
  const badge = [...field.querySelectorAll(".eoc-decision-badge.default")]
    .find((item) => item.textContent?.trim() === "Default: Ha Default");
  if (!badge) return false;
  badge.textContent = HOME_ASSISTANT_SESSION_DEFAULT;
  return true;
}

export function installManagementConversationDefaultLabel(Panel) {
  // A constructor is the production API; registry callers remain supported.
  if (typeof Panel !== "function") {
    const registry = Panel || globalThis.customElements;
    if (!registry?.whenDefined) return Promise.resolve(false);
    return registry.whenDefined("extended-openai-management-panel").then(() => installManagementConversationDefaultLabel(registry.get("extended-openai-management-panel")));
  }
  const constructor = Panel;
  const prototype = constructor?.prototype;
  if (!prototype || prototype[PATCHED]) return false;

  const originalRender = prototype._renderContent;
  prototype._renderContent = function(...args) {
    const result = originalRender.apply(this, args);
    queueMicrotask(() => normalizeConversationContinuityDefault(this));
    return result;
  };

  prototype[PATCHED] = true;
  return true;
}


export {HOME_ASSISTANT_SESSION_DEFAULT};
