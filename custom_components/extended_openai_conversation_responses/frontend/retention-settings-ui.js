import {applyTargetedConfigDirty} from "./management-state-safety.js";
import {clone, saveBarMarkup} from "./unsaved-state.js";

const FIELDS = [
  ["usage_request_retention_days", "Keep request details for"],
  ["usage_run_retention_days", "Keep run details for"],
];

function optionMarkup(panel, item, selected) {
  const value = item?.value ?? item;
  const label = item?.label ?? String(value);
  return `<option value="${panel._e(String(value))}" ${String(value) === String(selected) ? "selected" : ""}>${panel._e(label)}</option>`;
}

function selectMarkup(panel, key, label) {
  const value = panel._draft?.[key] ?? panel._result?.config?.[key];
  const choices = panel._result?.options?.[key] || [];
  return `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(`${label} usage history retention details ${key}`.toLowerCase())}">
    <label for="config-${key}">${panel._e(label)}</label>
    <select id="config-${key}" data-retention-config="${key}">${choices.map((item) => optionMarkup(panel, item, value)).join("")}</select>
    <span class="field-error" data-error="${key}"></span>
  </div>`;
}

function saveBar(panel) {
  return panel._configDirty
    ? saveBarMarkup({configuration: true, pending: Boolean(panel._configurationSaving)})
    : "";
}

export function renderRetentionSettings(panel) {
  return `<div class="content-card config-surface">
    <section class="config-section" id="section-retention" data-section="retention">
      <div class="section-heading">
        <div><h2>Usage history retention</h2><p>Choose how long detailed usage records are kept. Overall totals are kept separately.</p></div>
      </div>
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
}

export function bindRetentionSettings(panel) {
  const root = panel.shadowRoot;
  if (!root) return;

  root.querySelectorAll("[data-retention-config]").forEach((control) => {
    control.addEventListener("change", () => {
      const key = control.dataset.retentionConfig;
      if (!key || !panel._draft) return;
      panel._draft[key] = Number(control.value);
      applyTargetedConfigDirty(panel, [key], control, false);
      refreshSaveBar(panel);
    });
  });

  root.querySelector("#revert-config")?.addEventListener("click", () => {
    if (panel._configurationSaving || !panel._configData?.config) return;
    panel._draft = clone(panel._configData.config);
    panel._draftTitle = panel._configData.title;
    panel._setConfigDirty(false);
    panel._eocMainMarkup = null;
    panel._render();
  });
}
