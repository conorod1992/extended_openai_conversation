const PATCHED = Symbol.for("extended-openai.management-overview-health-clarity");

const count = (root, state) => root?.querySelectorAll?.(`.setup-health-check-${state}`)?.length || 0;

export function clarifySetupHealthSummary(panel) {
  const root = panel?.shadowRoot;
  const summary = root?.querySelector?.(".setup-health-summary");
  if (!summary) return false;

  const errors = count(root, "error");
  const warnings = count(root, "warning");
  const unknown = count(root, "unknown");
  const issues = errors + warnings;
  const title = summary.querySelector("strong");
  const detail = summary.querySelector("span");

  if (title) {
    title.textContent = errors
      ? "Needs attention"
      : warnings
        ? "Review recommended"
        : unknown
          ? "Status incomplete"
          : "Ready";
  }

  if (detail) {
    if (issues && unknown) {
      detail.textContent = `${issues} ${issues === 1 ? "issue" : "issues"} to review · ${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`;
    } else if (issues) {
      detail.textContent = `${issues} ${issues === 1 ? "issue" : "issues"} to review`;
    } else if (unknown) {
      detail.textContent = `${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`;
    } else {
      detail.textContent = "Core setup looks ready";
    }
  }
  return true;
}

export function installManagementOverviewHealthClarity(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      if (this._page === "overview") queueMicrotask(() => clarifySetupHealthSummary(this));
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementOverviewHealthClarity();
}
