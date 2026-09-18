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
