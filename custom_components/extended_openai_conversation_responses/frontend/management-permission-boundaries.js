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

