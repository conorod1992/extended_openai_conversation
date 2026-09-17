import "./management-settings-polish.js";

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

export function installManagementConversationDefaultLabel(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      queueMicrotask(() => normalizeConversationContinuityDefault(this));
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementConversationDefaultLabel();
}

export {HOME_ASSISTANT_SESSION_DEFAULT};
