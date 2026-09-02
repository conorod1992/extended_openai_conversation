import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [source, bootstrap] = await Promise.all([
  readFile(frontend("management-state-safety.js"), "utf8"),
  readFile(frontend("management-bootstrap.js"), "utf8"),
]);
const stateSafety = await import(frontend("management-state-safety.js"));
const bootstrapModule = await import(frontend("management-bootstrap.js"));

assert.match(bootstrap, /await import\("\.\/management-state-safety\.js"\)/);
assert.match(bootstrap, /await import\("\.\/debug-management\.js"\)/);
assert.match(bootstrap, /installPreDefinitionPropertyReplay\(constructor\)/);
assert.match(source, /SECTION_CACHE_TTL_MS = 30_000/);
assert.match(source, /Discard unsaved Guest policy changes\?/);
assert.match(source, /Switching agents will discard your unsaved Guest Mode policy changes/);
assert.match(source, /Explicit Cancel remains an intentional discard/);
assert.match(source, /window\.addEventListener\("beforeunload"/);
assert.match(source, /window\.addEventListener\("focus"/);
assert.match(source, /originalStartFreshGuestPolicy/);
assert.match(source, /preserveGuestDraft/);

class UpgradePanel {
  constructor() {
    this.hassCalls = 0;
    this.routeCalls = 0;
  }
  set hass(value) {
    this.hassCalls += 1;
    this._hass = value;
  }
  set route(value) {
    this.routeCalls += 1;
    this._route = value;
  }
}
assert.equal(bootstrapModule.installPreDefinitionPropertyReplay(UpgradePanel), true);
const upgradedPanel = new UpgradePanel();
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
assert.equal(upgradedPanel.hassCalls, 1);
assert.equal(upgradedPanel.routeCalls, 1);
assert.deepEqual(upgradedPanel._hass, {connected: true});
assert.deepEqual(upgradedPanel._route, {path: "/extended-openai/overview"});
assert.equal(
  bootstrapModule.installPreDefinitionPropertyReplay(UpgradePanel),
  false,
  "property replay patch must be idempotent",
);

const guestPanel = {
  _agentId: "agent-a",
  _guestDraft: {guest_mode_enabled: false, guest_excluded_entities: []},
  _viewKey: () => "capabilities/guest-mode",
};
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

const previousWindow = globalThis.window;
globalThis.window = {addEventListener() {}, removeEventListener() {}};
let networkLoads = 0;
class FakePanel {
  constructor() {
    this._sectionCache = new Map([["agent-a|capabilities/request-rules", {rules: ["stale"]}]]);
    this._eocSectionCacheTimes = new Map();
  }
  _viewKey() { return "capabilities/request-rules"; }
  _sectionCacheKey() { return "agent-a|capabilities/request-rules"; }
  async _loadSection() {
    const key = this._sectionCacheKey();
    if (!this._sectionCache.has(key)) {
      networkLoads += 1;
      this._sectionCache.set(key, {rules: [networkLoads]});
    }
  }
  async _navigate() {}
  async _handleRouteChange() {}
  async _startFreshGuestPolicy() {}
  _setupGuestSelectors() {}
  async _loadAgents() {}
  _invalidateAfterMutation() {}
  _render() {}
  disconnectedCallback() {}
}
const registry = {
  async whenDefined() {},
  get() { return FakePanel; },
};
assert.equal(await stateSafety.installManagementStateSafety(registry), true);
const cachePanel = new FakePanel();
await cachePanel._loadSection();
assert.equal(networkLoads, 1, "cache without a freshness timestamp must refresh");
await cachePanel._loadSection();
assert.equal(networkLoads, 1, "fresh cache should be reused");
const key = cachePanel._sectionCacheKey();
cachePanel._eocSectionCacheTimes.set(key, Date.now() - stateSafety.SECTION_CACHE_TTL_MS - 1);
await cachePanel._loadSection();
assert.equal(networkLoads, 2, "expired cache must refresh");

globalThis.window = previousWindow;
