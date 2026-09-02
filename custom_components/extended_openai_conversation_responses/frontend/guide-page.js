import "./management-state-safety.js";

let implementation = null;
let loadPromise = null;

if (typeof customElements !== "undefined") import("./management-loading-performance.js");

export async function ensureGuideModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./guide-page-impl.js")
      .then((module) => {
        implementation = module;
        return module;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}

if (typeof document === "undefined") await ensureGuideModule();

function queueRender(panel) {
  void ensureGuideModule()
    .then(() => panel._render?.())
    .catch((err) => {
      panel._error = `Unable to load Guide: ${err.message || String(err)}`;
      panel._render?.();
    });
}

export function renderGuide(panel) {
  if (implementation) return implementation.renderGuide(panel);
  queueRender(panel);
  return panel._loading?.() || '<div class="loading">Loading Guide…</div>';
}

export function bindGuide(panel) {
  return implementation?.bindGuide?.(panel);
}
