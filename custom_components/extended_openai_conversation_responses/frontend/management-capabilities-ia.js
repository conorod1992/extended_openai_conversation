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
  const agentId = panel._agentId;
  input.disabled = true;
  try {
    const result = await panel._call("knowledge", "set_enabled", {enabled: desired});
    if (panel._agentId !== agentId) return;
    const agent = panel._selectedAgent?.();
    if (agent) {
      agent.knowledge_enabled = result.knowledge_enabled;
      agent.feature_status = {
        ...(agent.feature_status || {}),
        knowledge: result.feature_status,
      };
    }
    if (panel._viewKey?.() === "data-memory/knowledge" && panel._result) {
      panel._result = {
        ...panel._result,
        feature_status: result.feature_status,
      };
    }
    panel._clearConfigDraft?.();
    panel._render();
    panel._toast(`Knowledge ${result.knowledge_enabled ? "enabled" : "disabled"}`);
  } catch (err) {
    input.checked = !desired;
    input.disabled = false;
    panel._toast(`Unable to update Knowledge: ${err.message || String(err)}`, true);
  } finally {
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
