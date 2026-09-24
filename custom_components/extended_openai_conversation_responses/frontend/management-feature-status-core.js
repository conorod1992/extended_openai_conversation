export function selectedFeatureStatus(panel, featureName) {
  const sectionStatus = panel._result?.feature_status;
  if (sectionStatus && !sectionStatus[featureName]) return sectionStatus;
  return sectionStatus?.[featureName] || panel._selectedAgent?.()?.feature_status?.[featureName] || null;
}

export function featureStatusMarkup(panel, title, status, configureTarget = null) {
  if (!status) return "";
  const positive = ["enabled", "available"].includes(status.state);
  const configure = panel._data?.is_admin && configureTarget
    ? `<button type="button" class="secondary inline-route" data-page="${panel._e(configureTarget.page)}" data-subsection="${panel._e(configureTarget.subsection)}">${panel._e(configureTarget.label)}</button>`
    : "";
  return `<section class="content-card feature-status-card"><div class="compact-status"><span><strong>${panel._e(title)}</strong><small>${panel._e(status.detail || status.summary || "")}</small></span><strong class="status-value ${positive ? "on" : ""}">${panel._e(status.label || "Unknown")}</strong></div>${configure}</section>`;
}

export function embeddedFeatureStatusMarkup(panel, title, status, configureTarget = null) {
  if (!status) return "";
  const positive = ["enabled", "available"].includes(status.state);
  const configure = panel._data?.is_admin && configureTarget
    ? `<button type="button" class="secondary inline-route" data-page="${panel._e(configureTarget.page)}" data-subsection="${panel._e(configureTarget.subsection)}">${panel._e(configureTarget.label)}</button>`
    : "";
  return `<div class="embedded-feature-status"><div class="compact-status"><span><strong>${panel._e(title)}</strong><small>${panel._e(status.detail || status.summary || "")}</small></span><strong class="status-value ${positive ? "on" : ""}">${panel._e(status.label || "Unknown")}</strong></div>${configure}</div>`;
}
