const PATCHED = Symbol.for("extended-openai.management-settings-polish");

const REDUNDANT_EFFECT_BADGES = new Set([
  "Advanced",
  "Adds context",
  "Stores data",
  "Stores shared data",
  "Stores temporary data",
]);

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

function pruneRedundantBadges(panel) {
  const root = panel?.shadowRoot;
  if (!root) return;

  root.querySelectorAll(".eoc-effect-badge").forEach((badge) => {
    if (REDUNDANT_EFFECT_BADGES.has(badge.textContent?.trim())) badge.remove();
  });

  const apiField = root.querySelector('[data-field="api_mode"]');
  const defaultBadge = apiField?.querySelector(".eoc-decision-badge.default");
  const recommendedBadge = apiField?.querySelector(".eoc-decision-badge.recommended");
  if (defaultBadge && recommendedBadge) {
    recommendedBadge.textContent = "Recommended default: Automatic (Auto)";
    defaultBadge.remove();
  }
}

export function polishSettingsLayout(panel) {
  ensureStyles(panel);
  pruneRedundantBadges(panel);
}

function schedulePolish(panel) {
  // Other management decorators also finish in microtasks. Queue one extra turn
  // so badge cleanup runs after those decorators regardless of import order.
  queueMicrotask(() => queueMicrotask(() => polishSettingsLayout(panel)));
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
      schedulePolish(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementSettingsPolish();
}
