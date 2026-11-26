import {applyTargetedConfigDirty} from "./management-state-safety.js";
import {saveBarMarkup} from "./unsaved-state.js";
import {discardConfigurationDraft} from "./configuration-acknowledgement.js";

const FIELDS = [
  ["usage_request_retention_days", "Keep request details for"],
  ["usage_run_retention_days", "Keep run details for"],
];

export function usageRetentionDeletionNotice(shortened) {
  const deletions = FIELDS.flatMap(([key], index) => {
    const days = shortened?.[key];
    const detail = index === 0 ? "request details" : "run details";
    if (!Number.isInteger(days) || days < 0) return [];
    return [days === 0 ? `all ${detail}` : `${detail} older than ${days} days`];
  });
  return deletions.length
    ? `Shorter Usage retention will delete ${deletions.join(" and ")}. Usage totals will be kept.`
    : "";
}

function selectMarkup(panel, key, label) {
  const value = panel._draft?.[key] ?? panel._result?.config?.[key];
  const choices = panel._result?.options?.[key] || [];
  const suspended = !choices.some(item => (item?.value ?? item) === value);
  const options = choices.map(item => {
    const option = item?.value ?? item;
    return `<option value="${panel._e(String(option))}" ${option === value ? "selected" : ""}>${panel._e(item?.label ?? String(option))}</option>`;
  });
  return `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(`${label} usage history retention details ${key}`.toLowerCase())}">
    <label for="config-${key}">${panel._e(label)}</label>
    <select id="config-${key}" data-config="${key}" data-retention-config="${key}" data-type="number">${suspended ? '<option value="" disabled selected>Suspended · History kept</option>' : ""}${options.join("")}</select>
    <small>${key === "usage_request_retention_days" ? "Individual provider calls." : "Completed conversation turns."}</small>
    <span class="field-error" data-error="${key}"></span>
  </div>`;
}

function saveBar(panel) {
  return panel._configDirty
    ? saveBarMarkup({configuration: true, pending: Boolean(panel._configurationSaving)})
    : "";
}

export function renderRetentionSettings(panel) {
  return `<section class="page-intro"><h1>Usage data retention</h1></section><div class="content-card retention-surface">
    <section id="config-retention" data-config-section data-search="usage history retention request run details totals">
      <div class="card-heading"><div>
        <h2>Retention periods</h2>
        <p>Set separate periods for request and run details. Saving Disabled immediately deletes existing detailed history of that type. Overall usage totals are kept.</p>
      </div></div>
      <div class="form-grid">
        ${FIELDS.map(([key, label]) => selectMarkup(panel, key, label)).join("")}
      </div>
    </section>
    ${saveBar(panel)}
    <span id="save-bar-anchor" class="sr-only"></span>
  </div>`;
}

function refreshSaveBar(panel) {
  const root = panel.shadowRoot;
  const existing = root?.querySelector?.(".save-bar");
  if (!panel._configDirty) {
    existing?.remove?.();
    return;
  }
  if (existing) return;
  root?.querySelector?.("#save-bar-anchor")?.insertAdjacentHTML(
    "beforebegin",
    saveBarMarkup({configuration: true, pending: Boolean(panel._configurationSaving)}),
  );
  bindDiscard(panel);
}

export function bindRetentionSettings(panel) {
  const root = panel.shadowRoot;
  if (!root) return;

  root.querySelectorAll("[data-retention-config]").forEach((control) => {
    control.addEventListener("change", () => {
      const key = control.dataset.retentionConfig;
      if (!key || !panel._draft || control.value === "") return;
      panel._draft[key] = Number(control.value);
      applyTargetedConfigDirty(panel, [key], control, false);
      refreshSaveBar(panel);
    });
  });

  bindDiscard(panel);
}

function bindDiscard(panel) {
  const button = panel.shadowRoot.querySelector("#revert-config");
  if (!button || button.dataset.eocDiscardBound) return;
  button.dataset.eocDiscardBound = "true";
  button.addEventListener("click", () => {
    if (panel._configurationSaving || !panel._configData?.config) return;
    discardConfigurationDraft(panel);
    panel._eocMainMarkup = null;
    panel._render();
  });
}
