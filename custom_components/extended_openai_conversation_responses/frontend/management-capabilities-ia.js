import {getConfigurationEditor} from "./management-route.js";

function renderConfiguration(panel, presentation) {
  const module = getConfigurationEditor();
  if (!module) return panel._loading?.() || '<div class="loading">Loading configuration…</div>';
  return module.renderConfiguration(panel, presentation);
}

function bindConfiguration(panel) {
  return getConfigurationEditor()?.bindConfiguration(panel);
}

function knowledgeAvailabilityMarkup(panel) {
  if (panel._data?.is_admin === false) return "";
  const sectionStatus = panel._result?.feature_status;
  const status = sectionStatus && typeof sectionStatus.enabled === "boolean"
    ? sectionStatus
    : panel._selectedAgent?.()?.feature_status?.knowledge;
  const enabled = typeof status?.enabled === "boolean" ? status.enabled : status?.state === "enabled";
  return `<section class="content-card knowledge-availability-setting">
    <div class="config-toggle setting">
      <span class="setting-copy"><span class="setting-label-row"><label for="knowledge-enabled-toggle"><strong>Allow the assistant to use Knowledge</strong></label></span><small>When off, stored sources remain in the library but Knowledge tools are not available to the assistant. Changes here save immediately.</small></span>
      <label class="switch-control" for="knowledge-enabled-toggle"><input id="knowledge-enabled-toggle" type="checkbox" role="switch" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>
    </div>
  </section>`;
}

function knowledgeSourceAvailabilityBadge(source) {
  const enabled = source?.enabled !== false;
  return `<span class="${enabled ? "availability-badge" : "disabled-badge"} knowledge-source-availability-badge">${enabled ? "Available" : "Unavailable"}</span>`;
}



export {knowledgeSourceAvailabilityControl} from "./management-dialogs.js";

async function saveKnowledgeAvailability(panel, input) {
  const desired = input.checked;
  input.disabled = true;
  try {
    const current = await panel._call("configuration", "get");
    const config = JSON.parse(JSON.stringify(current.config || {}));
    config.knowledge_enabled = desired;
    const validation = await panel._call("configuration", "validate", {config});
    if (!validation.valid) throw new Error(Object.values(validation.errors || {})[0] || "Configuration validation failed");
    await panel._call("configuration", "update", {config, title: current.title});
    panel._clearConfigDraft?.();
    await panel._loadAgents(panel._agentId);
    await panel._loadSection(true);
    panel._toast(`Knowledge ${desired ? "enabled" : "disabled"}`);
  } catch (err) {
    input.checked = !desired;
    input.disabled = false;
    panel._toast(`Unable to update Knowledge: ${err.message || String(err)}`, true);
  } finally {
    // Reconciliation can retain this control after a successful save.
    input.disabled = false;
  }
}

export function bindCapabilities(panel) {
  const view = panel._viewKey();
  if (["capabilities/home-assistant", "capabilities/web-skills"].includes(view)) bindConfiguration(panel);
  const knowledgeToggle = panel.shadowRoot.querySelector("#knowledge-enabled-toggle");
  if (knowledgeToggle && !knowledgeToggle.__eocKnowledgeBound) {
    knowledgeToggle.__eocKnowledgeBound = true;
    knowledgeToggle.addEventListener("change", () => saveKnowledgeAvailability(panel, knowledgeToggle));
  }
}

export {
  renderConfiguration,
  knowledgeAvailabilityMarkup,
  knowledgeSourceAvailabilityBadge,
};
