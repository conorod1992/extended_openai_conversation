export const DECISION_GUIDANCE_STYLES = `
    .eoc-decision-badge{display:inline-flex;align-items:center;min-height:20px;padding:2px 7px;border-radius:999px;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:11px;font-weight:650;line-height:1.25;white-space:nowrap}
    .eoc-decision-badge.recommended{border-color:color-mix(in srgb,var(--primary-color) 45%,var(--divider-color));color:var(--primary-text-color)}
    .eoc-confirm-scope,.eoc-restore-scope{margin:12px 0 0;padding:10px 12px;border-radius:8px;background:var(--secondary-background-color);color:var(--secondary-text-color);line-height:1.4}
    .eoc-confirm-scope strong,.eoc-restore-scope strong{color:var(--primary-text-color)}
    .eoc-more-phrases{opacity:.8}
    .eoc-live-request-test{margin-top:16px}
    .eoc-live-request-test>summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px;font-weight:650}
    .eoc-live-request-body{padding-top:14px}
    .eoc-live-request-result{margin-top:12px;white-space:pre-wrap;line-height:1.45}
    .eoc-live-label{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border-radius:999px;border:1px solid var(--error-color);color:var(--error-color);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
  `;

export function assistantScopeLabel(agent) {
  if (!agent) return "";
  const title = String(agent.title || "Unnamed assistant").trim();
  const entryTitle = String(agent.entry_title || "").trim();
  return entryTitle && entryTitle !== title ? `${title} — ${entryTitle}` : title;
}

function addScopeNode(container, className, agentLabel, subject = "") {
  if (!container) return;
  let node = container.querySelector(`.${className}`);
  if (!agentLabel) {
    node?.remove();
    return;
  }
  if (!node) {
    node = document.createElement("p");
    node.className = className;
    container.append(node);
  }
  node.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = "Assistant: ";
  node.append(strong, document.createTextNode(agentLabel));
  if (subject) node.append(document.createElement("br"), document.createTextNode(`Item: ${subject}`));
}


export function enhanceConfirmationScope(panel, subject = "") {
  const dialog = panel.shadowRoot?.querySelector("#confirm-dialog");
  const body = dialog?.querySelector(".dialog-body");
  addScopeNode(body, "eoc-confirm-scope", assistantScopeLabel(panel._selectedAgent?.()), subject);
}



