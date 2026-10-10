import assert from "node:assert/strict";

globalThis.window = {
  location: {pathname: "/extended-openai/capabilities/functions"},
  addEventListener() {},
  removeEventListener() {},
};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {
      hasChildNodes: () => false,
      querySelector: () => null,
      querySelectorAll: () => [],
    };
  }
};
let definedPanel;
globalThis.customElements = {
  define(_name, constructor) { definedPanel = constructor; },
  get() { return definedPanel; },
  whenDefined() { return Promise.resolve(); },
};

const {ExtendedOpenAIManagementPanel} = await import(
  "../custom_components/extended_openai_conversation_responses/frontend/management-panel.js"
);

const calls = [];
const panel = new ExtendedOpenAIManagementPanel();
panel._agentId = "agent-1";
panel._data = {agents: [{subentry_id: "agent-1"}], is_admin: true};
panel._configData = {revision: "revision-1", config: {functions: []}};
panel._sectionCache = new Map([
  ["agent-1|capabilities/request-rules", {function_catalog: ["stale"]}],
]);
panel._eocSectionCacheTimes = new Map([
  ["agent-1|capabilities/request-rules", Date.now()],
]);
panel._request = async (section, action, extra = {}) => {
  calls.push({section, action, extra});
  return action === "validate_current"
    ? {valid: true}
    : {revision: `revision-${calls.length + 1}`};
};

const first = await panel._call("tools", "save", {tool: {spec: {name: "one"}}});
assert.equal(calls[0].extra.revision, "revision-1");
assert.equal(first.revision, "revision-2");
assert.equal(panel._configData.revision, "revision-2");

await panel._call("tools", "set_enabled", {name: "one", enabled: false});
assert.equal(calls[1].extra.revision, "revision-2");
assert.equal(panel._configData.revision, "revision-3");

panel._invalidateAfterMutation("agent-1", "tools", "set_enabled");
assert.equal(
  panel._sectionCache.has("agent-1|capabilities/request-rules"),
  false,
  "Function Tool mutations must invalidate the Request Rules function catalogue",
);
assert.equal(
  panel._eocSectionCacheTimes.has("agent-1|capabilities/request-rules"),
  false,
);

panel._sectionCache.set("agent-1|capabilities/request-rules", {rules: ["old-order"]});
panel._eocSectionCacheTimes.set("agent-1|capabilities/request-rules", Date.now());
panel._invalidateAfterMutation("agent-1", "request_rules", "move");
assert.equal(
  panel._sectionCache.has("agent-1|capabilities/request-rules"),
  false,
  "moving a Request Rule must invalidate its cached ordering",
);

await panel._call("tools", "validate_current", {});
assert.equal(calls[2].extra.revision, undefined, "read-only tool calls need no revision");
assert.equal(panel._configData.revision, "revision-3");


for (const action of ["settings", "defaults", "wording_groups", "groups", "create", "update", "delete", "duplicate", "move", "rule_pack_import"]) {
  const key = "agent-1|capabilities/request-rules", other = "agent-2|capabilities/request-rules";
  panel._sectionCache.set(key, {revision:"old"}); panel._eocSectionCacheTimes.set(key, Date.now());
  panel._sectionCache.set(other, {revision:"other"}); panel._eocSectionCacheTimes.set(other, Date.now());
  panel._invalidateAfterMutation("agent-1", "request_rules", action);
  assert.equal(panel._sectionCache.has(key), false, `${action} affects the cached Request Rules route`);
  assert.equal(panel._eocSectionCacheTimes.has(key), false);
  assert.equal(panel._sectionCache.has(other), true, "other agents retain their cache");
}

for (const action of ["save", "save_one", "delete_one", "configuration_save"]) {
  const key = "entry-1|agent-1|full";
  panel._data.agents[0].entry_id = "entry-1";
  panel._cleanConfigSnapshots.set(key, {config: {functions: ["old"]}});
  panel._sectionCache.set("agent-1|capabilities/request-rules", {function_catalog: ["old"]});
  panel._invalidateAfterMutation("agent-1", "function_repair", action);
  assert.equal(panel._cleanConfigSnapshots.has(key), false,
    `${action} must invalidate the configuration snapshot after Function Repair`);
  assert.equal(panel._sectionCache.has("agent-1|capabilities/request-rules"), false,
    "repaired tools must refresh the Request Rules function catalogue too");
}
