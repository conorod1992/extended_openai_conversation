import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [source, panelSource] = await Promise.all([
  readFile(frontend("management-state-safety.js"), "utf8"),
  readFile(frontend("management-panel.js"), "utf8"),
]);
const stateSafety = await import(frontend("management-state-safety.js"));

assert.doesNotMatch(source, /installManagementStateSafety/);
assert.doesNotMatch(source, /prototype\./);
assert.match(panelSource, /from "\.\/management-state-safety\.js"/);
assert.doesNotMatch(panelSource, /management-bootstrap\.js|initializeManagementPanel/);
assert.match(panelSource, /connectedCallback\(\)/);
assert.match(panelSource, /Object\.prototype\.hasOwnProperty\.call\(this, name\)/);
assert.equal(stateSafety.SECTION_CACHE_TTL_MS, 30_000);
assert.match(source, /Discard unsaved changes\?/);
assert.match(source, /pageCoordinator\(panel\)\.leaving\(destination\)/);
assert.match(source, /Explicit Cancel remains an intentional discard/);
assert.match(source, /window\.addEventListener\("beforeunload"/);
assert.match(source, /window\.addEventListener\("focus"/);
assert.match(panelSource, /initializePageDraft\(this\)/);
assert.match(panelSource, /bindStateSafety\(this\)/);
assert.match(panelSource, /bindPageDrafts\(this\)/);
assert.match(panelSource, /refreshPageSaveBar\(this\)/);
assert.match(panelSource, /confirmStateSafeNavigation\(this, destination\)/);
assert.match(panelSource, /cleanupStateSafety\(this\)/);

const windowListeners = new Map();
globalThis.window = {
  location: {pathname: "/extended-openai/overview"},
  addEventListener(type, listener) { windowListeners.set(type, listener); },
  removeEventListener(type, listener) {
    if (windowListeners.get(type) === listener) windowListeners.delete(type);
  },
};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {
  style = {};
  attachShadow() {
    this.shadowRoot = {
      hasChildNodes: () => false,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener() {},
      dispatchEvent() {},
    };
  }
};
let definedPanel;
globalThis.customElements = {
  define(_name, constructor) { definedPanel = constructor; },
  get() { return definedPanel; },
  whenDefined() { return Promise.resolve(); },
};

const {ExtendedOpenAIManagementPanel} = await import(frontend("management-panel.js"));
const upgradedPanel = new ExtendedOpenAIManagementPanel();
let hassReplays = 0;
let routeRenders = 0;
upgradedPanel._loadAgents = () => { hassReplays += 1; };
upgradedPanel._render = () => { routeRenders += 1; };
Object.defineProperty(upgradedPanel, "hass", {
  configurable: true,
  enumerable: true,
  writable: true,
  value: {connected: true},
});
Object.defineProperty(upgradedPanel, "route", {
  configurable: true,
  enumerable: true,
  writable: true,
  value: {path: "/extended-openai/overview"},
});
upgradedPanel.connectedCallback();
assert.equal(Object.hasOwn(upgradedPanel, "hass"), false);
assert.equal(Object.hasOwn(upgradedPanel, "route"), false);
assert.equal(hassReplays, 1);
assert.equal(routeRenders, 1);
assert.deepEqual(upgradedPanel._hass, {connected: true});
assert.deepEqual(upgradedPanel._route, {path: "/extended-openai/overview"});
assert.equal(windowListeners.has("beforeunload"), true);
assert.equal(windowListeners.has("focus"), true);
assert.equal(windowListeners.has("popstate"), true);
// Back/Forward reach the same guarded route transition as HA route updates.
const routes = [];
upgradedPanel._handleRouteChange = async (route) => {
  routes.push(route.section);
  upgradedPanel._page = route.page;
  upgradedPanel._subsection = route.section;
};
window.location.pathname = "/extended-openai/assistant/basics";
windowListeners.get("popstate")();
upgradedPanel.route = {path: window.location.pathname};
await upgradedPanel._routeSyncPromise;
assert.deepEqual(routes, ["basics"], "duplicate HA notifications must not repeat the transition");
window.location.pathname = "/extended-openai/assistant/model-responses";
windowListeners.get("popstate")();
await upgradedPanel._routeSyncPromise;
assert.deepEqual(routes, ["basics", "model-responses"]);
// A second browser move while a route load awaits must converge on the latest URL.
let releaseRoute;
const routeGate = new Promise((resolve) => { releaseRoute = resolve; });
upgradedPanel._handleRouteChange = async (route) => {
  await routeGate;
  upgradedPanel._page = route.page;
  upgradedPanel._subsection = route.section;
};
window.location.pathname = "/extended-openai/assistant/basics";
windowListeners.get("popstate")();
await Promise.resolve();
window.location.pathname = "/extended-openai/assistant/prompt-context";
windowListeners.get("popstate")();
releaseRoute();
await upgradedPanel._routeSyncPromise;
assert.equal(upgradedPanel._subsection, "prompt-context");
// The real handler must release route synchronization before a backend read settles.
upgradedPanel._handleRouteChange = ExtendedOpenAIManagementPanel.prototype._handleRouteChange;
upgradedPanel._hydrateCleanConfiguration = () => true;
let releaseRead;
const readGate = new Promise((resolve) => { releaseRead = resolve; });
upgradedPanel._loadSection = () => readGate;
window.location.pathname = "/extended-openai/assistant/basics";
windowListeners.get("popstate")();
await upgradedPanel._routeSyncPromise;
assert.equal(upgradedPanel._subsection, "basics");
window.location.pathname = "/extended-openai/assistant/prompt-context";
windowListeners.get("popstate")();
await upgradedPanel._routeSyncPromise;
assert.equal(upgradedPanel._subsection, "prompt-context", "pending data must not block the next route");
releaseRead();
await readGate;
// Exercise the real discard guard: a declined navigation restores the current URL.
upgradedPanel._handleRouteChange = ExtendedOpenAIManagementPanel.prototype._handleRouteChange;
upgradedPanel._configDirty = true;
upgradedPanel._draftAgentId = "agent-a";
upgradedPanel._agentId = "agent-a";
let discardPrompts = 0;
upgradedPanel._confirm = async () => { discardPrompts += 1; return false; };
const {pageCoordinator} = await import(frontend("management-page-drafts.js"));
pageCoordinator(upgradedPanel).register("history-test-editor", {
  dirty: () => true,
  owns: () => false,
  discard: () => { throw new Error("declined navigation discarded the editor"); },
});
history.pushState = (_state, _title, path) => { window.location.pathname = path; };
window.location.pathname = "/extended-openai/guide";
windowListeners.get("popstate")();
await upgradedPanel._routeSyncPromise;
assert.equal(upgradedPanel._subsection, "prompt-context");
assert.equal(window.location.pathname, "/extended-openai/assistant/prompt-context");
assert.equal(discardPrompts, 1);
window.location.pathname = "/lovelace";
windowListeners.get("popstate")();
assert.equal(upgradedPanel._page, "assistant", "ignore history outside the EOAI panel");
upgradedPanel.disconnectedCallback();
assert.equal(windowListeners.has("beforeunload"), false);
assert.equal(windowListeners.has("focus"), false);
assert.equal(windowListeners.has("popstate"), false);

const guestPanel = {
  _agentId: "agent-a",
  _guestDraft: {guest_mode_enabled: false, guest_excluded_entities: []},
  _viewKey: () => "capabilities/guest-mode",
  _result: {config: {guest_mode_enabled: false, guest_excluded_entities: []}},
};
(await import(frontend("management-page-drafts.js"))).initializePageDraft(guestPanel);
assert.equal(stateSafety.syncGuestDirty(guestPanel), false);
guestPanel._guestDraft.guest_mode_enabled = true;
assert.equal(stateSafety.syncGuestDirty(guestPanel), true);
guestPanel._guestDraft.guest_mode_enabled = false;
assert.equal(stateSafety.syncGuestDirty(guestPanel), false);

const control = {
  id: "rule-name",
  name: "",
  type: "text",
  tagName: "INPUT",
  checked: false,
  value: "Good night",
};
const ruleDialog = {
  id: "rule-dialog",
  querySelectorAll: () => [control],
};
const baseline = [{id: "rule-name", type: "text", checked: false, value: "Good night"}];
assert.equal(stateSafety.dialogHasUnsavedChanges(ruleDialog, baseline), false);
control.value = "Bedtime";
assert.equal(stateSafety.dialogHasUnsavedChanges(ruleDialog, baseline), true);

const yaml = {value: "spec:\n  name: example"};
const toolDialog = {
  id: "tool-dialog",
  querySelector: () => yaml,
  querySelectorAll: () => [],
};
assert.equal(
  stateSafety.dialogHasUnsavedChanges(toolDialog, null, "spec:\n  name: example"),
  false,
);
yaml.value += "\n  description: changed";
assert.equal(
  stateSafety.dialogHasUnsavedChanges(toolDialog, null, "spec:\n  name: example"),
  true,
);

// Native host cache expiry is exercised in management-performance-ui.test.mjs.
