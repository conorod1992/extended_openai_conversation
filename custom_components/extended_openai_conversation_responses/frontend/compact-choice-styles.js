export const COMPACT_CHOICE_STYLE = `.group-function-choices{display:grid;gap:8px;max-height:300px;overflow:auto;margin-top:12px}.group-function-choice{display:grid;grid-template-columns:auto 1fr;align-items:start;gap:11px;padding:11px;border:1px solid var(--divider-color);border-radius:9px;color:var(--primary-text-color);cursor:pointer}.group-function-choice.is-disabled{opacity:.58}.group-function-choice:hover{background:var(--secondary-background-color)}.group-function-choice input{width:18px;min-height:18px;margin-top:2px}.group-function-choice span{display:grid;gap:3px}.group-function-choice small{overflow-wrap:anywhere}`;

export function ensureCompactChoiceStyle(panel) {
  if (typeof document === "undefined" || !panel?.shadowRoot) return;
  if (panel.shadowRoot.querySelector('style[data-eoc-feature-style="compact-choices"]')) return;
  const style = document.createElement("style");
  style.dataset.eocFeatureStyle = "compact-choices";
  style.textContent = COMPACT_CHOICE_STYLE;
  panel.shadowRoot.append(style);
}
