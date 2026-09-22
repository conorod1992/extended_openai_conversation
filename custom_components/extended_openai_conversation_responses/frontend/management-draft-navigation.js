import {dirtyConfigurationDestinations} from "./management-config-destinations.js";
import {enhancementChanged} from "./management-enhancement-state.js";

function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-configuration-clarity]")) return;
  const style = document.createElement("style");
  style.dataset.eocConfigurationClarity = "";
  style.textContent = `
    .agent-picker.eoc-agent-context{padding:10px 12px;border:1px solid color-mix(in srgb,var(--primary-color) 35%,var(--divider-color));border-radius:12px;background:color-mix(in srgb,var(--primary-color) 5%,var(--card-background-color));box-shadow:0 1px 3px rgba(0,0,0,.06);min-width:min(330px,100%)}
    .agent-picker.eoc-agent-context>span{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--secondary-text-color)}
    .agent-picker.eoc-agent-context select{font-weight:700;font-size:15px}
    .agent-picker.eoc-agent-context small{font-weight:500}
    .eoc-effect-badge{display:inline-flex;align-items:center;min-height:20px;padding:2px 7px;border-radius:999px;background:var(--secondary-background-color);border:1px solid var(--divider-color);color:var(--secondary-text-color);font-size:11px;font-weight:650;line-height:1.25;white-space:nowrap}
    .setting-label-row{flex-wrap:wrap}
    .top-nav button.eoc-has-unsaved::after,.subsection-nav button.eoc-has-unsaved::after{content:"";display:inline-block;width:7px;height:7px;margin-left:7px;border-radius:50%;background:var(--primary-color);vertical-align:middle}
    .agent-picker.eoc-agent-context.eoc-has-unsaved{border-color:color-mix(in srgb,var(--primary-color) 60%,var(--divider-color))}
    @media (max-width:800px){.agent-picker.eoc-agent-context{width:100%;box-sizing:border-box;min-width:0}}
  `;
  root.append(style);
}

function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

function enhanceAgentContext(panel, destinations) {
  const root = panel.shadowRoot;
  const picker = root?.querySelector(".agent-picker");
  const agent = panel._selectedAgent?.();
  if (!picker) return;
  picker.classList.add("eoc-agent-context");
  picker.classList.toggle("eoc-has-unsaved", destinations.size > 0);
  const heading = picker.querySelector(":scope > span");
  setText(heading, "Editing assistant");
  const select = picker.querySelector("#agent");
  if (select) select.setAttribute("aria-label", "Editing assistant");
  const draftActive = panel._draft && panel._draftAgentId === panel._agentId;
  const detail = picker.querySelector("small");
  if (detail && agent) {
    const model = draftActive ? panel._draft.chat_model || agent.model : agent.model;
    setText(detail, `${agent.provider} · ${model}${destinations.size ? " · Unsaved changes" : ""}`);
  }
}

function setDirtyMarker(element, dirty, label) {
  if (!element) return;
  element.classList?.toggle?.("eoc-has-unsaved", dirty);
  if (dirty) element.setAttribute?.("aria-label", `${label}, has unsaved changes`);
  else element.removeAttribute?.("aria-label");
}

function optionBaseLabel(option) {
  if (!option) return "";
  if (!option.dataset.eocBaseLabel) option.dataset.eocBaseLabel = option.textContent.replace(/\s+•$/, "");
  return option.dataset.eocBaseLabel;
}

function enhanceDirtyNavigation(panel, destinations) {
  const root = panel.shadowRoot;
  if (!root) return;
  const dirtyPages = new Set([...destinations].map((item) => item.split("/", 1)[0]));
  root.querySelectorAll(".top-nav button[data-page]").forEach((button) => {
    const label = button.textContent.replace(/\s+•$/, "").trim();
    setDirtyMarker(button, dirtyPages.has(button.dataset.page), label);
  });
  root.querySelectorAll(".subsection-nav button[data-subsection]").forEach((button) => {
    const label = button.textContent.replace(/\s+•$/, "").trim();
    setDirtyMarker(button, destinations.has(`${panel._page}/${button.dataset.subsection}`), label);
  });
  const topMobile = root.querySelector("#top-section-mobile");
  topMobile?.querySelectorAll("option").forEach((option) => {
    const base = optionBaseLabel(option);
    setText(option, dirtyPages.has(option.value) ? `${base} •` : base);
  });
  const local = root.querySelector("#local-section");
  local?.querySelectorAll("option").forEach((option) => {
    const base = optionBaseLabel(option);
    setText(option, destinations.has(`${panel._page}/${option.value}`) ? `${base} •` : base);
  });
}

export function enhanceConfigurationClarity(panel) {
  if (!panel.shadowRoot) return;
  const destinations = dirtyConfigurationDestinations(panel);
  const agent = panel._selectedAgent?.();
  const model = panel._draft && panel._draftAgentId === panel._agentId ? panel._draft.chat_model || agent?.model : agent?.model;
  if (!enhancementChanged(panel, "configuration-clarity", [panel._agentId, panel._page, panel._subsection, agent?.provider, model, [...destinations].sort().join("|"), panel._eocNavigationRevision])) return;
  ensureStyles(panel);
  enhanceAgentContext(panel, destinations);
  enhanceDirtyNavigation(panel, destinations);
}

export function bindConfigurationClarity(panel) {
  const root = panel.shadowRoot;
  if (!root || root.__eocClarityInteractionBound) return;
  root.__eocClarityInteractionBound = true;
  // State safety dispatches this after updating authoritative dirty state.
  root.addEventListener("eoc-config-dirty-changed", () => enhanceConfigurationClarity(panel));
}
