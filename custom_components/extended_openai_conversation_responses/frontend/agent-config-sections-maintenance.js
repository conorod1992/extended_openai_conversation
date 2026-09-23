// Generated from agent-config-editor-base.js by scripts/generate-agent-config-sections.mjs.
import {section, field, select, toggle, option, labelRow, settingSearch,
  configurationChoiceLabel, renderLocalHandling, regexRow, modelDataControls, helpButton}
  from "./agent-config-editor-base.js";
export function renderConfigurationSections(panel, {voiceIdentity = null, renderExposedAttributes = null, renderBackup = null} = {}) {
  const config = panel._draft || panel._result?.config || {};
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
  return `
    ${section(panel,"retention","Usage history retention","Choose how long detailed usage records are kept. Overall totals are kept separately.","usage request run details totals",() => `<div class="form-grid">${select(panel,"usage_request_retention_days","Keep request details for",config.usage_request_retention_days,choices("usage_request_retention_days"),"")}${select(panel,"usage_run_retention_days","Keep run details for",config.usage_run_retention_days,choices("usage_run_retention_days"),"")}</div>`)}
    ${section(panel,"backup","Export, Backup, Import & Restore","Move reusable setup or durable agent data safely between agents and installations.","disaster recovery migration memories knowledge usage private",() => renderBackup ? renderBackup(Boolean(panel._configDirty)) : "")}
  `;
}
