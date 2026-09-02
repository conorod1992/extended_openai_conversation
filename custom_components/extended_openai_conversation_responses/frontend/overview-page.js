let implementation = null;
let loadPromise = null;

export async function ensureOverviewModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./overview-page-impl.js")
      .then((module) => {
        implementation = module;
        return module;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}

if (typeof document === "undefined") await ensureOverviewModule();

function queueRender(panel) {
  void ensureOverviewModule()
    .then(() => panel._render?.())
    .catch((err) => {
      panel._error = `Unable to load Overview: ${err.message || String(err)}`;
      panel._render?.();
    });
}

export function renderOverview(panel, agent) {
  if (implementation) return implementation.renderOverview(panel, agent);
  queueRender(panel);
  return panel._loading?.() || '<div class="loading">Loading Overview…</div>';
}

export function bindOverview(panel) {
  return implementation?.bindOverview?.(panel);
}
