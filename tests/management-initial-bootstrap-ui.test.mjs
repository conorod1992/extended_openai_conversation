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

resolveBroadcast({enabled:false, can_manage:true, catalog:{}, history:[]});
resolveAgents({agents: [selectedAgent], is_admin: true});
await Promise.resolve();
await Promise.resolve();
assert.equal(panel.rendered, true, "agent catalogue should render a useful Overview before the summary settles");
assert.equal(panel._result, null, "progressive first paint must not invent detailed Overview data");
assert.equal(panel.fallbackLoads || 0, 0);
resolveOverview({agent: {...selectedAgent, model: "gpt-test"}, usage: {today: {total_tokens: 12}}, conversations: {}, load_errors: []});
await load;
assert.equal(panel.fallbackLoads || 0, 0);
assert.equal(panel._result.usage.today.total_tokens, 12);
assert.equal(panel._selectedAgent().model, "gpt-test");
assert.equal(storage.get(module.ENTRY_KEY), "entry-a");
