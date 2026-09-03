const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai.management-permission-boundaries");

export function isRestrictedManagementView(page, subsection = null) {
  if (page === "data-memory" && subsection === "knowledge") return true;
  if (page === "usage-maintenance" && subsection === null) return true;
  if (page === "usage-maintenance" && ["usage", "diagnostics"].includes(subsection)) return true;
  return false;
}

export function nonAdminOverviewKnowledgeSnapshot(panel) {
  const agent = panel?._selectedAgent?.();
  return {
    sources: [],
    stats: {source_count: Number(agent?.knowledge_source_count || 0)},
  };
}

export function installManagementPermissionBoundaries(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined(PANEL_TAG).then(() => {
    const constructor = registry.get(PANEL_TAG);
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalCanAccessView = prototype._canAccessView;
    prototype._canAccessView = function(page, subsection = null) {
      if (this._data?.is_admin === false && isRestrictedManagementView(page, subsection)) {
        return false;
      }
      return originalCanAccessView.call(this, page, subsection);
    };

    const originalCall = prototype._call;
    prototype._call = function(section, action, extra = {}) {
      // Overview intentionally remains useful to normal HA users. It only needs
      // the already-exposed source count, not the agent-global Knowledge records.
      if (
        this._data?.is_admin === false
        && this._viewKey?.() === "overview"
        && section === "knowledge"
        && action === "list"
      ) {
        return Promise.resolve(nonAdminOverviewKnowledgeSnapshot(this));
      }
      return originalCall.call(this, section, action, extra);
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementPermissionBoundaries();
}
