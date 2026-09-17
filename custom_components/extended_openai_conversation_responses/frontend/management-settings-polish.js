const PATCHED = Symbol.for("extended-openai.management-settings-polish");

function ensureStyles(panel) {
  const root = panel?.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-settings-polish]")) return;
  const style = document.createElement("style");
  style.dataset.eocSettingsPolish = "";
  style.textContent = `
    .guide-quick-start,
    .guide-quick-tasks,
    .guide-quick-card,
    .guide-groups,
    .guide-group,
    .guide-topics,
    .guide-topic{min-width:0;max-width:100%}
    .guide-quick-tasks{grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))!important}
    .guide-quick-card{width:100%;overflow-wrap:anywhere}
    [data-field="conversation_continuity"]{align-content:start}
    #config-conversation_continuity{height:42px;min-height:42px}
  `;
  root.append(style);
}

export function polishSettingsLayout(panel) {
  ensureStyles(panel);
}

export function installManagementSettingsPolish(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      queueMicrotask(() => polishSettingsLayout(this));
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementSettingsPolish();
}
