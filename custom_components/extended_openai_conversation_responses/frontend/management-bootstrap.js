import {installManagementCopyPolish} from "./agent-config-loader.js";
import {installManagementBrowser} from "./guest-mode-ui.js";
import {installManagementStateSafety} from "./management-state-safety.js";
import {installManagementActionSafety} from "./management-action-safety.js";
import {installFunctionDependencyIntegrity} from "./management-function-dependencies.js";
import {installManagementFeatureStatus} from "./management-feature-status.js";
import {installManagementTemporaryMemory} from "./management-temporary-memory.js";
import {installManagementMemorySettings} from "./management-memory-settings.js";
import {installManagementCapabilitiesIA} from "./management-capabilities-ia.js";
import {installManagementVoiceIdentity} from "./management-voice-identity.js";
import {installManagementPermissionBoundaries} from "./management-permission-boundaries.js";
import {installManagementNavigationSearch} from "./management-navigation-search.js";
import {installManagementToolbarLayout} from "./management-toolbar-layout.js";
import {installManagementConfigurationClarity} from "./management-configuration-clarity.js";
import {installManagementConfigurationGuidance} from "./management-configuration-guidance.js";
import {installManagementDecisionGuidance} from "./management-decision-guidance.js";
import {installManagementConversationDefaultLabel} from "./management-conversation-default-label.js";
import {installManagementSettingsPolish} from "./management-settings-polish.js";
import {installManagementOverviewHealthClarity} from "./management-overview-health-clarity.js";
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

// Run the remaining shared extensions before define() upgrades any existing host.
// No methods on the browser's CustomElementRegistry are replaced.
export function initializeManagementPanel(Panel) {
  installManagementCopyPolish(Panel);
  installManagementBrowser(Panel);
  installManagementStateSafety(Panel);
  installManagementActionSafety(Panel);
  installFunctionDependencyIntegrity(Panel);
  installManagementFeatureStatus(Panel);
  installManagementTemporaryMemory(Panel);
  installManagementMemorySettings(Panel);
  installManagementCapabilitiesIA(Panel);
  installManagementVoiceIdentity(Panel);
  installManagementPermissionBoundaries(Panel);
  installManagementNavigationSearch(Panel);
  installManagementToolbarLayout(Panel);
  installManagementConfigurationClarity(Panel);
  installManagementConfigurationGuidance(Panel);
  installManagementDecisionGuidance(Panel);
  installManagementConversationDefaultLabel(Panel);
  installManagementSettingsPolish(Panel);
  installManagementOverviewHealthClarity(Panel);
  installPreDefinitionPropertyReplay(Panel);
  installManagementHotPathPerformance(Panel);
}

export {installManagementHotPathPerformance, installPreDefinitionPropertyReplay};
