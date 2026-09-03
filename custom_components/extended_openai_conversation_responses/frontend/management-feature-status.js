const PATCHED = Symbol.for("extended-openai.management-feature-status");

function selectedFeatureStatus(panel, featureName) {
  const sectionStatus = panel._result?.feature_status;
  if (sectionStatus && !sectionStatus[featureName]) return sectionStatus;
  return sectionStatus?.[featureName] || panel._selectedAgent?.()?.feature_status?.[featureName] || null;
}

export function featureStatusMarkup(panel, title, status, configureTarget = null) {
  if (!status) return "";
  const positive = ["enabled", "available"].includes(status.state);
  const target = configureTarget || {page: "assistant", subsection: "advanced", label: "Configure Memory & Knowledge"};
  const configure = panel._data?.is_admin
    ? `<button type="button" class="secondary inline-route" data-page="${panel._e(target.page)}" data-subsection="${panel._e(target.subsection)}">${panel._e(target.label)}</button>`
    : "";
  return `<section class="content-card feature-status-card"><div class="compact-status"><span><strong>${panel._e(title)}</strong><small>${panel._e(status.detail || status.summary || "")}</small></span><strong class="status-value ${positive ? "on" : ""}">${panel._e(status.label || "Unknown")}</strong></div>${configure}</section>`;
}

export function overviewAgentFeatureProjection(agent) {
  const memory = agent?.feature_status?.memory;
  const knowledge = agent?.feature_status?.knowledge;
  if (!memory && !knowledge) return agent;
  const memoryLabel = memory?.label || agent.memory_mode || "Unknown";
  const labels = [memoryLabel];
  if (knowledge?.label) labels.push(`Knowledge ${knowledge.label}`);
  return {...agent, memory_mode: labels.join(" · ")};
}

export function installManagementFeatureStatus(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalMemories = prototype._memories;
    prototype._memories = function(...args) {
      const content = originalMemories.apply(this, args);
      if (this._memoryKind === "temporary") return content;
      return `${featureStatusMarkup(this, "Persistent memory", selectedFeatureStatus(this, "memory"), {page: "data-memory", subsection: "memory-settings", label: "Configure memory"})}${content}`;
    };

    const originalKnowledge = prototype._knowledge;
    prototype._knowledge = function(...args) {
      return `${featureStatusMarkup(this, "Knowledge Library", selectedFeatureStatus(this, "knowledge"), {page: "assistant", subsection: "advanced", label: "Configure Knowledge"})}${originalKnowledge.apply(this, args)}`;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementFeatureStatus();
}
