import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const source = await readFile(frontend("management-route.js"), "utf8") + await readFile(frontend("management-renderer.js"), "utf8");
assert.match(source, /panel\?\._data !== null/);
assert.match(source, /main\.dataset\.eocInitialLoading/);
assert.match(source, /panel\._loading\?\.\(\)/);
assert.match(source, /Promise\.allSettled\(\[/);
assert.match(source, /section: "overview"/);
assert.match(source, /action: "summary"/);
assert.match(source, /type: WS_TYPE, action: "agents"/);
assert.match(source, /extended-openai-agent-entry/);

const module = {...await import(frontend("management-route.js")), ...await import(frontend("management-renderer.js"))};
assert.equal(module.needsFullConfiguration("data-memory/memory-settings"), true);
assert.equal(module.needsFullConfiguration("capabilities/home-assistant"), true);
assert.equal(module.needsFullConfiguration("data-memory/conversations", false), false);
assert.equal(module.needsFullConfiguration("usage-maintenance/retention"), false);
assert.equal(module.needsFullConfiguration("overview"), false);

const loadingMain = {
  innerHTML: "<div class=\"empty\">No conversation agents configured.</div>",
  dataset: {},
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
};
const loadingPanel = {
  _data: null,
  _loading: () => '<div class="loading">Loading…</div>',
  shadowRoot: {querySelector: (selector) => selector === "main" ? loadingMain : null},
};
assert.equal(module.showInitialLoading(loadingPanel), true);
assert.match(loadingMain.innerHTML, /Loading/);
assert.equal(loadingMain.attributes["aria-busy"], "true");
assert.equal("eocInitialLoading" in loadingMain.dataset, true);

const emptyMain = {
  innerHTML: '<div class="empty">No conversation agents configured.</div>',
  dataset: {},
  setAttribute() {},
};
const emptyPanel = {
  _data: {agents: []},
  _loading: () => '<div class="loading">Loading…</div>',
  shadowRoot: {querySelector: () => emptyMain},
};
assert.equal(module.showInitialLoading(emptyPanel), false);
assert.match(emptyMain.innerHTML, /No conversation agents configured/);

const storage = new Map([
  [module.AGENT_KEY, "agent-a"],
  [module.ENTRY_KEY, "entry-a"],
]);
globalThis.localStorage = {
  getItem(key) { return storage.get(key) || null; },
  setItem(key, value) { storage.set(key, value); },
};

let resolveAgents;
let resolveOverview;
let resolveBroadcast;
const calls = [];
const agentsPromise = new Promise((resolve) => { resolveAgents = resolve; });
const overviewPromise = new Promise((resolve) => { resolveOverview = resolve; });
const broadcastPromise = new Promise((resolve) => { resolveBroadcast = resolve; });
const selectedAgent = {entry_id: "entry-a", subentry_id: "agent-a", title: "A"};
const panel = {
  _hass: {
    callWS(payload) {
      calls.push(payload);
      if (payload.action === "agents") return agentsPromise;
      if (payload.action === "snapshot") return broadcastPromise;
      return overviewPromise;
    },
  },
  _viewKey: () => "overview",
  _agentId: null,
  _scopeCatalogCache: new Map(),
  _scopeCatalogKey: () => null,
  _applyScopes() {},
  _selectedAgent() { return this._data?.agents?.find((item) => item.subentry_id === this._agentId); },
  _render() { this.rendered = true; },
  async _loadSection() { this.fallbackLoads = (this.fallbackLoads || 0) + 1; },
};

const load = module.loadAgentsWithOverviewPrefetch(panel);
await Promise.resolve();
assert.equal(calls.length, 3);
assert.equal(calls.some((item) => item.action === "agents"), true);
assert.equal(calls.some((item) => item.section === "overview" && item.action === "summary"), true);
assert.equal(calls.some((item) => item.action === "snapshot"), true);


const assistantStorage = new Map([
  [module.AGENT_KEY, "agent-a"],
  [module.ENTRY_KEY, "entry-a"],
]);
globalThis.localStorage = {
  getItem(key) { return assistantStorage.get(key) || null; },
  setItem(key, value) { assistantStorage.set(key, value); },
};

let resolveAssistantAgents;
let resolveAssistantConfig;
const assistantCalls = [];
const assistantAgentsPromise = new Promise((resolve) => { resolveAssistantAgents = resolve; });
const assistantConfigPromise = new Promise((resolve) => { resolveAssistantConfig = resolve; });
const assistantPanel = {
  _hass: {
    callWS(payload) {
      assistantCalls.push(payload);
      if (payload.action === "agents") return assistantAgentsPromise;
      if (payload.section === "configuration" && payload.action === "get") return assistantConfigPromise;
      throw new Error(`Unexpected Assistant bootstrap call: ${JSON.stringify(payload)}`);
    },
  },
  _viewKey: () => "assistant/model-responses",
  _agentId: null,
  _loadToken: 0,
  _scopeCatalogCache: new Map(),
  _scopeCatalogKey: () => null,
  _applyScopes() {},
  _setConfigDirty(value) { this.configDirty = value; },
  _selectedAgent() { return this._data?.agents?.find((item) => item.subentry_id === this._agentId); },
  async _loadSection() { this.sectionLoads = (this.sectionLoads || 0) + 1; },
};

const assistantLoad = module.loadAgentsWithOverviewPrefetch(assistantPanel);
await Promise.resolve();
assert.equal(assistantCalls.some((item) => item.action === "agents"), true);
assert.equal(
  assistantCalls.some((item) => item.section === "configuration" && item.action === "get"),
  true,
  "Assistant configuration should start alongside the agent catalogue",
);
resolveAssistantConfig({
  title: "A",
  revision: "r1",
  config: {chat_model: "gpt-test"},
  defaults: {},
  options: {},
  model_capabilities: {},
  function_types: [],
});
resolveAssistantAgents({agents: [{entry_id:"entry-a",subentry_id:"agent-a",title:"A"}], is_admin: true});
await assistantLoad;
assert.equal(assistantPanel._configData.config.chat_model, "gpt-test");
assert.equal(assistantPanel._draft.chat_model, "gpt-test");
assert.equal(assistantPanel._draftAgentId, "agent-a");
assert.equal(assistantPanel.sectionLoads, 1);

// A Memory Settings deep link begins the same full configuration read before
// the catalogue settles, then reuses that authoritative selected-agent result.
const memoryCalls = [];
let finishMemoryAgents;
const memoryPanel = {
  ...assistantPanel,
  _viewKey: () => "data-memory/memory-settings",
  _data: null,
  _agentId: null,
  _configData: null,
  sectionLoads: 0,
  _hass: {callWS(payload) {
    memoryCalls.push(payload);
    if (payload.action === "agents") return new Promise((resolve) => { finishMemoryAgents = resolve; });
    if (payload.section === "configuration") return Promise.resolve({title:"Memory", config:{memory_mode:"manual"}});
    throw new Error("Unexpected Memory request");
  }},
};
const memoryLoad = module.loadAgentsWithOverviewPrefetch(memoryPanel);
assert.equal(memoryCalls.some((item) => item.section === "configuration" && item.action === "get"), true);
finishMemoryAgents({agents:[selectedAgent], is_admin:true});
await memoryLoad;
assert.equal(memoryPanel._draft.memory_mode, "manual");
assert.equal(memoryPanel.sectionLoads, 1);

let resolveStaleAgents;
let resolveStaleConfig;
const staleAgentsPromise = new Promise((resolve) => { resolveStaleAgents = resolve; });
const staleConfigPromise = new Promise((resolve) => { resolveStaleConfig = resolve; });
const stalePanel = {
  ...assistantPanel,
  _data: null,
  _agentId: null,
  _configData: null,
  _draft: null,
  _draftAgentId: null,
  sectionLoads: 0,
  _hass: {
    callWS(payload) {
      if (payload.action === "agents") return staleAgentsPromise;
      if (payload.section === "configuration" && payload.action === "get") return staleConfigPromise;
      throw new Error("Unexpected stale-prefetch call");
    },
  },
};
const staleLoad = module.loadAgentsWithOverviewPrefetch(stalePanel);
resolveStaleConfig({title:"A", config:{chat_model:"stale"}});
resolveStaleAgents({agents:[{entry_id:"entry-b",subentry_id:"agent-b",title:"B"}],is_admin:true});
await staleLoad;
assert.equal(stalePanel._configData, null, "stale stored Assistant config must not be applied");
assert.equal(stalePanel._agentId, "agent-b");
assert.equal(stalePanel.sectionLoads, 1);
