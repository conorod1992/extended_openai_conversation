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
    [data-eoc-guide-layout]{grid-template-columns:minmax(0,1fr)!important}
    [data-eoc-guide-layout]>*{min-width:0;max-width:100%}
    .guide-quick-start,
    .guide-quick-tasks,
    .guide-quick-card,
    .guide-search,
    .guide-groups,
    .guide-group,
    .guide-group-heading,
    .guide-topics,
    .guide-topic{min-width:0;max-width:100%}
    .guide-quick-tasks{grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr))!important}
    .guide-quick-card{width:100%;overflow-wrap:anywhere}
    .comparison-table{min-width:0;max-width:100%;overflow-x:auto}
    .comparison-table table{max-width:none}
    [data-field="conversation_continuity"]{align-content:start}
    #config-conversation_continuity{height:42px;min-height:42px}
    .eoc-model-data-panel{display:grid;gap:14px;margin-top:30px;padding-top:26px;border-top:1px solid var(--divider-color)}
    .eoc-model-data-heading{display:grid;gap:6px}
    .eoc-model-data-heading h3{margin:0;color:var(--primary-text-color);font-size:16px}
    .eoc-model-data-heading p{max-width:860px;margin:0;color:var(--secondary-text-color);font-size:13px;line-height:1.5}
    .eoc-model-data-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .eoc-model-data-action{display:flex;min-width:0;flex-direction:column;align-items:flex-start;gap:7px;padding:14px;border:1px solid var(--divider-color);border-radius:10px;background:color-mix(in srgb,var(--secondary-background-color) 32%,var(--card-background-color))}
    .eoc-model-data-action strong{color:var(--primary-text-color);font-size:13px;line-height:1.35}
    .eoc-model-data-action p{flex:1;margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.45}
    .eoc-model-data-action button{width:100%;margin-top:4px}
    .eoc-model-data-status{display:grid;gap:3px;margin:0;padding:10px 12px;border-radius:9px;background:var(--secondary-background-color);font-size:12px;line-height:1.45}
    .eoc-model-data-status strong{color:var(--primary-text-color)}
    .eoc-model-data-status [data-model-data-status]{margin:0}
    @media(max-width:900px){.eoc-model-data-actions{grid-template-columns:1fr}}
  `;
  root.append(style);
}

function applyGuideLayout(panel) {
  const root = panel?.shadowRoot;
  if (!root) return;
  const main = root.querySelector("[data-eoc-main]") || root.querySelector("main");
  if (!main) return;
  if (root.querySelector(".guide-quick-start")) main.dataset.eocGuideLayout = "";
  else delete main.dataset.eocGuideLayout;
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

function modelActionCard(button, title, text) {
  if (!button) return null;
  const card = document.createElement("div");
  card.className = "eoc-model-data-action";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const copy = document.createElement("p");
  copy.textContent = text;
  button.classList.remove("compact-button", "section-reset");
  card.append(heading, copy, button);
  return card;
}

function enhanceModelDataPanel(panel) {
  const root = panel?.shadowRoot;
  const section = root?.querySelector("#config-model");
  if (!section || section.querySelector("[data-eoc-model-data-panel]")) return;

  const updateData = section.querySelector('[data-model-data="update"]');
  const useBundled = section.querySelector('[data-model-data="reset"]');
  const status = section.querySelector("[data-model-data-status]");
  if (!updateData || !useBundled || !status) return;

  const oldActions = updateData.closest(".section-actions");
  const panelNode = document.createElement("div");
  panelNode.className = "eoc-model-data-panel";
  panelNode.dataset.eocModelDataPanel = "";

  const heading = document.createElement("div");
  heading.className = "eoc-model-data-heading";
  const title = document.createElement("h3");
  title.textContent = "Model capability data";
  const intro = document.createElement("p");
  intro.textContent = "Extended OpenAI uses shared capability data to decide which parameters and API features each model supports. Update checks can be run manually here, while bundled data provides a known-good fallback shipped with the integration.";
  heading.append(title, intro);

  const actions = document.createElement("div");
  actions.className = "eoc-model-data-actions";
  const cards = [
    modelActionCard(
      updateData,
      "Check for model data updates",
      "Check the remote model catalogue now instead of waiting for the next scheduled check.",
    ),
    modelActionCard(
      useBundled,
      "Restore bundled data",
      "Return to the model capability data shipped with this installed integration.",
    ),
  ];
  actions.append(...cards);

  const statusBox = document.createElement("div");
  statusBox.className = "eoc-model-data-status";
  const statusTitle = document.createElement("strong");
  statusTitle.textContent = "Model data status";
  statusBox.append(statusTitle, status);

  oldActions?.remove();
  panelNode.append(heading, actions, statusBox);
  section.append(panelNode);
}

export function polishSettingsLayout(panel) {
  ensureStyles(panel);
  applyGuideLayout(panel);
  pruneRedundantBadges(panel);
  enhanceModelDataPanel(panel);
}

function schedulePolish(panel) {
  if (panel._eocSettingsPolishScheduled) return;
  panel._eocSettingsPolishScheduled = true;
  // Other management decorators also finish in microtasks. Queue one extra turn
  // so cleanup and structural polish run after them regardless of import order.
  queueMicrotask(() => queueMicrotask(() => {
    panel._eocSettingsPolishScheduled = false;
    polishSettingsLayout(panel);
  }));
}

function bindInteractionPolish(panel) {
  const root = panel?.shadowRoot;
  if (!root || root.__eocSettingsPolishBound) return;
  root.__eocSettingsPolishBound = true;
  root.addEventListener("input", () => schedulePolish(panel));
  root.addEventListener("change", () => schedulePolish(panel));
  root.addEventListener("value-changed", () => schedulePolish(panel));
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
      bindInteractionPolish(this);
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
