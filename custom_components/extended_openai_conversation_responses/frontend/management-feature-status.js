const PATCHED = Symbol.for("extended-openai.management-feature-status");

function selectedFeatureStatus(panel, featureName) {
  const sectionStatus = panel._result?.feature_status;
  if (sectionStatus && !sectionStatus[featureName]) return sectionStatus;
  return sectionStatus?.[featureName] || panel._selectedAgent?.()?.feature_status?.[featureName] || null;
}

export function featureStatusMarkup(panel, title, status) {
  if (!status) return "";
  const positive = ["enabled", "available"].includes(status.state);
  const configure = panel._data?.is_admin
    ? `<button type="button" class="secondary inline-route" data-page="assistant" data-subsection="advanced">Configure Memory & Knowledge</button>`
    : "";
  return `<section class="content-card feature-status-card"><div class="compact-status"><span><strong>${panel._e(title)}</strong><small>${panel._e(status.detail || status.summary || "")}</small></span><strong class="status-value ${positive ? "on" : ""}">${panel._e(status.label || "Unknown")}</strong></div>${configure}</section>`;
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
      return `${featureStatusMarkup(this, "Persistent memory", selectedFeatureStatus(this, "memory"))}${content}`;
    };

    const originalKnowledge = prototype._knowledge;
    prototype._knowledge = function(...args) {
      return `${featureStatusMarkup(this, "Knowledge Library", selectedFeatureStatus(this, "knowledge"))}${originalKnowledge.apply(this, args)}`;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementFeatureStatus();
}
