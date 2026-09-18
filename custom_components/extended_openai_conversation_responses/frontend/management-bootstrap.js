const PANEL_TAG = "extended-openai-management-panel";
const PROPERTY_REPLAY_PATCHED = Symbol.for("extended-openai.management-property-replay");
const HOT_PATH_PATCHED = Symbol.for("extended-openai.management-hot-path-performance");
const NAVIGATION_MARK_PREFIX = "extended-openai:navigation";
const LOAD_MARK_PREFIX = "extended-openai:load-section";
const RENDER_MARK_PREFIX = "extended-openai:render";
const MAX_MEASURE_ENTRIES = 100;
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
const BOOTSTRAP_MODULES = [
  "./management-state-safety.js",
  "./management-action-safety.js",
  "./management-function-dependencies.js",
  "./management-feature-status.js",
  "./management-memory-settings.js",
  "./management-capabilities-ia.js",
  "./management-voice-identity.js",
  "./management-permission-boundaries.js",
  "./management-function-repair.js",
  "./management-navigation-search.js",
  "./management-toolbar-layout.js",
  "./management-history-pagination.js",
  "./usage-input-footprint.js",
  "./debug-management.js",
  "./management-configuration-clarity.js",
  "./management-configuration-guidance.js",
  "./management-decision-guidance.js",
  "./management-conversation-default-label.js",
  "./management-settings-polish.js",
  "./management-overview-health-clarity.js",
];

function preloadBootstrapModules(documentRef = globalThis.document) {
  const head = documentRef?.head;
  if (!head?.append) return;
  for (const specifier of BOOTSTRAP_MODULES) {
    const href = new URL(specifier, import.meta.url).href;
    if (documentRef.querySelector?.(`link[rel="modulepreload"][href="${href}"]`)) continue;
    const link = documentRef.createElement("link");
    link.rel = "modulepreload";
    link.href = href;
    head.append(link);
  }
}

function installPreDefinitionPropertyReplay(constructor) {
  const prototype = constructor?.prototype;
  if (!prototype || prototype[PROPERTY_REPLAY_PATCHED]) return false;
  prototype[PROPERTY_REPLAY_PATCHED] = true;

  const originalConnected = prototype.connectedCallback;
  prototype.connectedCallback = function(...args) {
    // Home Assistant may create an unknown custom-panel element and assign these
    // properties before the module has finished defining the element. Those own
    // properties shadow the class setters after upgrade unless they are replayed.
    for (const name of ["hass", "route"]) {
      if (!Object.prototype.hasOwnProperty.call(this, name)) continue;
      const value = this[name];
      delete this[name];
      this[name] = value;
    }
    return originalConnected?.apply(this, args);
  };
  return true;
}

function capturePreRegistrationInstallers(registry) {
  if (!registry || registry.get?.(PANEL_TAG)) return () => {};
  const hadOwnDefine = Object.prototype.hasOwnProperty.call(registry, "define");
  const hadOwnGet = Object.prototype.hasOwnProperty.call(registry, "get");
  const hadOwnWhenDefined = Object.prototype.hasOwnProperty.call(registry, "whenDefined");
  const previousDefine = registry.define;
  const previousGet = registry.get;
  const previousWhenDefined = registry.whenDefined;
  const nativeDefine = previousDefine.bind(registry);
  const nativeGet = previousGet.bind(registry);
  const nativeWhenDefined = previousWhenDefined.bind(registry);
  const pending = [];
  let restored = false;

  const restoreProperty = (name, value, hadOwn) => {
    if (hadOwn) registry[name] = value;
    else delete registry[name];
  };
  const restore = () => {
    if (restored) return;
    restored = true;
    restoreProperty("define", previousDefine, hadOwnDefine);
    restoreProperty("get", previousGet, hadOwnGet);
    restoreProperty("whenDefined", previousWhenDefined, hadOwnWhenDefined);
  };

  registry.whenDefined = function(name) {
    if (name !== PANEL_TAG) return nativeWhenDefined(name);
    return {
      then(onFulfilled) {
        if (typeof onFulfilled === "function") pending.push(onFulfilled);
        return Promise.resolve(false);
      },
    };
  };

  registry.define = function(name, constructor, options) {
    if (name !== PANEL_TAG) return nativeDefine(name, constructor, options);

    restoreProperty("whenDefined", previousWhenDefined, hadOwnWhenDefined);
    restoreProperty("define", previousDefine, hadOwnDefine);
    registry.get = function(candidate) {
      return candidate === PANEL_TAG ? constructor : nativeGet(candidate);
    };
    try {
      installPreDefinitionPropertyReplay(constructor);
      for (const install of pending.splice(0)) install();
    } finally {
      restoreProperty("get", previousGet, hadOwnGet);
      restored = true;
    }
    return nativeDefine(name, constructor, options);
  };

  return restore;
}

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
  return {api, id, start, panel};
}

function rememberMeasure(measure) {
  const {api, id, panel} = measure;
  panel._eocPerformanceMeasureIds ||= [];
  panel._eocPerformanceMeasureIds.push(id);
  while (panel._eocPerformanceMeasureIds.length > MAX_MEASURE_ENTRIES) {
    const expired = panel._eocPerformanceMeasureIds.shift();
    api.clearMeasures?.(expired);
  }
}

function finishMeasure(measure, detail = null) {
  if (!measure) return;
  const {api, id, start} = measure;
  const end = `${id}:end`;
  api.mark(end);
  try {
    api.measure(id, {start, end, detail});
  } catch (_err) {
    // Older Performance implementations accept mark names rather than the
    // PerformanceMeasureOptions object. Timing must never break navigation.
    api.measure(id, start, end);
  }
  rememberMeasure(measure);
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
    && panel._eocNavigationDepth > 0
    && main
    && main.childNodes.length
    && !main.querySelector?.(".loading")
  );
  if (!canPreserve) return originalRender.apply(panel, args);

  // A populated view already communicates useful context while a user-initiated
  // destination loads. Keep it mounted instead of building throwaway loading DOM.
  // Other busy renders (including Home Assistant reconnect/restart recovery) must
  // run normally so the panel can rebuild and rebind itself after lifecycle events.
  ensureBusyStyle(root);
  main.setAttribute("aria-busy", "true");
  main.inert = true;
  main.classList.add("eoc-loading-in-background");
  return undefined;
}

function clearBusyPresentation(panel) {
  const main = panel.shadowRoot?.querySelector?.("[data-eoc-main]") || panel.shadowRoot?.querySelector?.("main");
  if (!main || panel._busy) return;
  main.removeAttribute("aria-busy");
  main.inert = false;
  main.classList.remove("eoc-loading-in-background");
}

function wrapAsyncMethod(prototype, name, prefix, navigation = false) {
  const original = prototype[name];
  if (typeof original !== "function") return;
  prototype[name] = function(...args) {
    const view = this._viewKey?.() || null;
    const measure = startMeasure(this, prefix);
    if (navigation) this._eocNavigationDepth = (this._eocNavigationDepth || 0) + 1;
    const finish = (status) => {
      if (navigation) this._eocNavigationDepth = Math.max(0, (this._eocNavigationDepth || 1) - 1);
      finishMeasure(measure, {view, status});
    };
    let result;
    try {
      result = original.apply(this, args);
    } catch (err) {
      finish("threw");
      throw err;
    }
    if (!result || typeof result.finally !== "function") {
      finish("sync");
      return result;
    }
    return result.finally(() => finish("settled"));
  };
}

function installManagementHotPathPerformance(Panel) {
  const prototype = Panel?.prototype;
  if (!prototype || prototype[HOT_PATH_PATCHED]) return false;
  prototype[HOT_PATH_PATCHED] = true;

  wrapAsyncMethod(prototype, "_navigate", NAVIGATION_MARK_PREFIX, true);
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

if (typeof customElements !== "undefined") {
  const restore = capturePreRegistrationInstallers(customElements);
  try {
    // Fetch the independent patch modules concurrently, while preserving their
    // deterministic evaluation/installation order below.
    preloadBootstrapModules();
    await import("./management-state-safety.js");
    await import("./management-action-safety.js");
    await import("./management-function-dependencies.js");
    await import("./management-feature-status.js");
    await import("./management-memory-settings.js");
    await import("./management-capabilities-ia.js");
    await import("./management-voice-identity.js");
    await import("./management-permission-boundaries.js");
    await import("./management-function-repair.js");
    await import("./management-navigation-search.js");
    await import("./management-toolbar-layout.js");
    // Retained Conversation data has explicit result pages independent of the
    // general management renderer, so install its navigation before registration.
    await import("./management-history-pagination.js");
    await import("./usage-input-footprint.js");
    // Request debugging extends the management panel too. Install that extension
    // before registration so the route cannot depend on a later microtask race.
    await import("./debug-management.js");
    // Configuration clarity decorates the final persistent shell/navigation; the
    // guidance layers build on those labels and badges without duplicating them.
    await import("./management-configuration-clarity.js");
    await import("./management-configuration-guidance.js");
    await import("./management-decision-guidance.js");
    // Keep default badges aligned with the labels users can actually select.
    await import("./management-conversation-default-label.js");
    // Apply the final settings-layout cleanup after the badge-producing layers.
    await import("./management-settings-polish.js");
    // Distinguish actionable health issues from checks whose status is unavailable.
    await import("./management-overview-health-clarity.js");
    // Register this last so it observes the fully wrapped management methods and
    // retains loaded content outside the existing rendering optimization.
    customElements.whenDefined(PANEL_TAG).then(() => installManagementHotPathPerformance(customElements.get(PANEL_TAG)));
  } catch (err) {
    restore();
    throw err;
  }
}

export {
  BOOTSTRAP_MODULES,
  capturePreRegistrationInstallers,
  installManagementHotPathPerformance,
  installPreDefinitionPropertyReplay,
  preloadBootstrapModules,
};
