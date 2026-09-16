const PATCHED = Symbol.for("extended-openai.management-hot-path-performance");
const NAVIGATION_MARK_PREFIX = "extended-openai:navigation";
const LOAD_MARK_PREFIX = "extended-openai:load-section";
const RENDER_MARK_PREFIX = "extended-openai:render";
const BUSY_STYLE = `
  [data-eoc-main].eoc-loading-in-background,
  main.eoc-loading-in-background {
    position: relative;
  }
  [data-eoc-main].eoc-loading-in-background::before,
  main.eoc-loading-in-background::before {
    content: "";
    position: absolute;
    z-index: 3;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: var(--primary-color);
    transform-origin: left center;
    animation: eoc-background-load 900ms ease-in-out infinite alternate;
    pointer-events: none;
  }
  @keyframes eoc-background-load {
    from { transform: scaleX(.18); opacity: .55; }
    to { transform: scaleX(1); opacity: .9; }
  }
`;

function nowId(panel, kind) {
  panel._eocPerformanceSequence = (panel._eocPerformanceSequence || 0) + 1;
  return `${kind}:${panel._eocPerformanceSequence}`;
}

function performanceApi() {
  const api = globalThis.performance;
  return api && typeof api.mark === "function" && typeof api.measure === "function" ? api : null;
}

function startMeasure(panel, prefix) {
  const api = performanceApi();
  if (!api) return null;
  const id = nowId(panel, prefix);
  const start = `${id}:start`;
  api.mark(start);
  return {api, id, start};
}

function finishMeasure(measure, detail = null) {
  if (!measure) return;
  const {api, id, start} = measure;
  const end = `${id}:end`;
  api.mark(end);
  api.measure(id, {
    start,
    end,
    detail,
  });
  api.clearMarks(start);
  api.clearMarks(end);
}

function ensureBusyStyle(root) {
  if (!root || root.querySelector?.("style[data-eoc-hot-path-performance]")) return;
  const style = document.createElement("style");
  style.dataset.eocHotPathPerformance = "";
  style.textContent = BUSY_STYLE;
  root.append(style);
}

function preserveBusyMain(panel, originalRender, args) {
  const root = panel.shadowRoot;
  const main = root?.querySelector?.("[data-eoc-main]") || root?.querySelector?.("main");
  const canPreserve = Boolean(
    panel._busy
    && main
    && main.childNodes.length
    && !main.querySelector?.(".loading")
  );
  if (!canPreserve) return originalRender.apply(panel, args);

  ensureBusyStyle(root);
  const fragment = document.createDocumentFragment();
  while (main.firstChild) fragment.append(main.firstChild);

  try {
    return originalRender.apply(panel, args);
  } finally {
    const currentMain = root.querySelector?.("[data-eoc-main]") || root.querySelector?.("main");
    if (!currentMain) return;
    currentMain.replaceChildren(fragment);
    currentMain.setAttribute("aria-busy", "true");
    currentMain.classList.add("eoc-loading-in-background");
  }
}

function clearBusyPresentation(panel) {
  const main = panel.shadowRoot?.querySelector?.("[data-eoc-main]") || panel.shadowRoot?.querySelector?.("main");
  if (!main || panel._busy) return;
  main.removeAttribute("aria-busy");
  main.classList.remove("eoc-loading-in-background");
}

function wrapAsyncMethod(prototype, name, prefix) {
  const original = prototype[name];
  if (typeof original !== "function") return;
  prototype[name] = function(...args) {
    const view = this._viewKey?.() || null;
    const measure = startMeasure(this, prefix);
    let result;
    try {
      result = original.apply(this, args);
    } catch (err) {
      finishMeasure(measure, {view, status: "threw"});
      throw err;
    }
    if (!result || typeof result.finally !== "function") {
      finishMeasure(measure, {view, status: "sync"});
      return result;
    }
    return result.finally(() => finishMeasure(measure, {view, status: "settled"}));
  };
}

function install(Panel) {
  const prototype = Panel?.prototype;
  if (!prototype || prototype[PATCHED]) return false;
  prototype[PATCHED] = true;

  wrapAsyncMethod(prototype, "_navigate", NAVIGATION_MARK_PREFIX);
  wrapAsyncMethod(prototype, "_loadSection", LOAD_MARK_PREFIX);

  const originalRender = prototype._render;
  if (typeof originalRender === "function") {
    prototype._render = function(...args) {
      const view = this._viewKey?.() || null;
      const busy = Boolean(this._busy);
      const measure = startMeasure(this, RENDER_MARK_PREFIX);
      try {
        return preserveBusyMain(this, originalRender, args);
      } finally {
        clearBusyPresentation(this);
        finishMeasure(measure, {view, busy});
      }
    };
  }
  return true;
}

export function installManagementHotPathPerformance(registry = globalThis.customElements) {
  if (typeof document === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => install(registry.get("extended-openai-management-panel")));
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementHotPathPerformance();
}
