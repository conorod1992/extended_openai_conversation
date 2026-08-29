import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

globalThis.window = {
  location: {pathname: "/extended-openai/assistant/basics"},
  addEventListener() {},
  removeEventListener() {},
};
globalThis.history = {pushState() {}};
globalThis.localStorage = {
  values: new Map(),
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) { this.values.set(key, value); },
};
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = {hasChildNodes: () => false}; }
};
let definedPanel;
globalThis.customElements = {
  define(_name, constructor) { definedPanel = constructor; },
  get() { return definedPanel; },
  whenDefined() { return Promise.resolve(); },
};

const [{ExtendedOpenAIManagementPanel}, {bindRequestRules}] = await Promise.all([
  import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js"),
  import("../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js"),
]);

const agents = [
  {entry_id:"entry-a", subentry_id:"agent-a", title:"A"},
  {entry_id:"entry-b", subentry_id:"agent-b", title:"B"},
];
const initialScopes = [{scope_id:"user:current", scope_type:"user", display_name:"Current", is_current_user:true}];

function panelFor(page = "assistant", subsection = "basics") {
  const panel = new ExtendedOpenAIManagementPanel();
  panel._page = page;
  panel._subsection = subsection;
  panel._data = {agents, scopes:initialScopes, is_admin:true};
  panel._agentId = "agent-a";
  panel._scopeId = "user:current";
  panel.renderStates = [];
  panel._render = () => panel.renderStates.push(panel._busy);
  return panel;
}

{
  const panel = panelFor();
  panel._configData = {title:"A", config:{model:"cached"}};
  panel._draft = {model:"cached"};
  panel._draftTitle = "A";
  panel._draftAgentId = "agent-a";
  let calls = 0;
  panel._hass = {callWS: async () => { calls += 1; throw new Error("configuration should stay cached"); }};
  await panel._loadSection();
  assert.equal(calls, 0);
  assert.deepEqual(panel.renderStates, [false]);
  assert.equal(panel._busy, false);

  panel._agentId = "agent-b";
  panel._hass = {callWS: async (message) => {
    assert.equal(message.action, "get");
    assert.equal(message.subentry_id, "agent-b");
    return {title:"B", config:{model:"fresh"}};
  }};
  panel.renderStates = [];
  await panel._loadSection();
  assert.equal(panel._draftAgentId, "agent-b");
  assert.equal(panel._draft.model, "fresh");
  assert.equal(panel._result.config.model, "fresh");
  assert.deepEqual(panel.renderStates, [true, false]);
}

{
  const panel = panelFor("overview", null);
  const calls = [];
  panel._hass = {callWS: async (message) => {
    calls.push(message);
    if (message.action === "agents") return {agents, scopes:initialScopes, is_admin:true};
    return {};
  }};
  panel._loadSection = async () => {};
  await panel._loadAgents("agent-a");
  assert.deepEqual(calls.map((call) => call.action), ["agents"]);
  assert.equal(panel._scopeId, "user:current");
}

{
  const panel = panelFor("data-memory", "memories");
  assert.equal(panel._sectionCacheKey(), null);
  const calls = [];
  panel._hass = {callWS: async (message) => {
    calls.push(message);
    if (message.section === "scopes") return {scopes:[{...initialScopes[0], memory_count:1, conversation_count:0}, {scope_id:"shared", scope_type:"shared", display_name:"Shared", memory_count:0, conversation_count:0}]};
    if (message.section === "memories") return {memories:[{memory_id:`memory-${message.subentry_id}`}]};
    return {};
  }};
  await panel._loadSection();
  assert.deepEqual(calls.map((call) => call.section), ["scopes", "memories"]);
  panel.renderStates = [];
  await panel._loadSection();
  assert.deepEqual(calls.map((call) => call.section), ["scopes", "memories", "memories"]);
  assert.deepEqual(panel.renderStates, [true, false]);

  panel._memoryKind = "temporary";
  await panel._loadSection();
  panel._scopeId = "shared";
  await panel._loadSection();
  assert.deepEqual(calls.map((call) => call.section), ["scopes", "memories", "memories", "memories", "memories"]);
  assert.deepEqual(calls.slice(3, 5).map((call) => [call.action, call.scope_id]), [
    ["temporary_list", "user:current"],
    ["temporary_list", "shared"],
  ]);

  panel._page = "guide";
  panel._subsection = null;
  await panel._loadSection();
  panel._page = "data-memory";
  panel._subsection = "memories";
  await panel._loadSection();
  assert.deepEqual(calls.slice(5).map((call) => call.section), ["scopes", "memories"]);

  panel._agentId = "agent-b";
  panel._scopeId = "user:current";
  panel._applyScopes(initialScopes);
  await panel._loadSection();
  assert.deepEqual(calls.slice(7).map((call) => [call.section, call.subentry_id]), [
    ["scopes", "agent-b"],
    ["memories", "agent-b"],
  ]);
  assert.equal(panel._sectionCache.size, 0);
}

{
  const panel = panelFor("capabilities", "request-rules");
  panel._sectionCache.set("agent-a|capabilities/request-rules", {rules:[{id:"one"}]});
  panel._sectionCache.set("agent-b|capabilities/request-rules", {rules:[{id:"two"}]});
  panel._hass = {callWS: async () => ({rule:{id:"one"}})};
  await panel._call("request_rules", "update", {rule_id:"one", rule:{}});
  assert.equal(panel._sectionCache.has("agent-a|capabilities/request-rules"), false);
  assert.equal(panel._sectionCache.has("agent-b|capabilities/request-rules"), true);

  panel._sectionCache.set("agent-a|data-memory/knowledge", {sources:[]});
  panel._sectionCache.set("agent-b|data-memory/knowledge", {sources:[]});
  panel._invalidateAfterMutation("agent-a", "knowledge", "create");
  assert.equal(panel._sectionCache.has("agent-a|data-memory/knowledge"), false);
  assert.equal(panel._sectionCache.has("agent-b|data-memory/knowledge"), true);

  panel._scopeCatalogCache.set("agent-a|data-memory/memories", initialScopes);
  panel._scopeCatalogCache.set("agent-b|data-memory/memories", initialScopes);
  panel._scopeCatalogVisitKey = "agent-a|data-memory/memories";
  panel._invalidateAfterMutation("agent-a", "memories", "delete");
  assert.equal(panel._scopeCatalogCache.has("agent-a|data-memory/memories"), false);
  assert.equal(panel._scopeCatalogCache.has("agent-b|data-memory/memories"), true);
  assert.equal(panel._scopeCatalogVisitKey, null);
}

{
  const panel = panelFor("data-memory", "conversations");
  panel._data.is_admin = false;
  const calls = [];
  panel._hass = {callWS: async (message) => {
    calls.push(message);
    if (message.section === "scopes") return {scopes:initialScopes};
    if (message.section === "conversations") return {sessions:[], settings:{}};
    return {};
  }};
  await panel._loadSection();
  await panel._loadSection();
  assert.equal(calls.filter((call) => call.section === "scopes").length, 1);

  panel._page = "guide";
  panel._subsection = null;
  await panel._loadSection();
  panel._page = "data-memory";
  panel._subsection = "conversations";
  await panel._loadSection();
  assert.equal(calls.filter((call) => call.section === "scopes").length, 2);
}

{
  const panel = panelFor("capabilities", "request-rules");
  let resolveRules;
  panel._hass = {callWS: () => new Promise((resolve) => { resolveRules = resolve; })};
  const oldLoad = panel._loadSection();
  panel._page = "data-memory";
  panel._subsection = "knowledge";
  const knowledge = {sources:[{source_id:"current"}]};
  panel._sectionCache.set("agent-a|data-memory/knowledge", knowledge);
  await panel._loadSection();
  resolveRules({rules:[{id:"old"}]});
  await oldLoad;
  assert.equal(panel._result, knowledge);
}

{
  const panel = panelFor("capabilities", "request-rules");
  let calls = 0;
  panel._hass = {callWS: async (message) => {
    calls += 1;
    assert.equal(message.section, "service_catalog");
    return {services:{light:{turn_on:{name:"Turn on", fields:{}}}}};
  }};
  const [first, second] = await Promise.all([panel._loadServiceCatalog(), panel._loadServiceCatalog()]);
  assert.equal(first, second);
  assert.equal(await panel._loadServiceCatalog(), first);
  assert.equal(calls, 1);
}

{
  let shadowRootListeners = 0;
  const shadowRoot = {
    addEventListener() { shadowRootListeners += 1; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const panel = {shadowRoot, _result:{rules:[]}, _serviceCatalog:null};
  bindRequestRules(panel);
  bindRequestRules(panel);
  assert.equal(shadowRootListeners, 0);
}

const management = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
const requestRules = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js", import.meta.url), "utf8");
assert.match(management, /_loadServiceCatalog\(\)/);
assert.doesNotMatch(requestRules, /result\.service_catalog/);
assert.doesNotMatch(requestRules, /root\.addEventListener\("click"/);
assert.match(requestRules, /id="rule-action-sequence-host"/);
assert.doesNotMatch(requestRules, /<ha-selector id="rule-action-sequence"/);
assert.match(requestRules, /selector = \{action:\{\}\}/);
