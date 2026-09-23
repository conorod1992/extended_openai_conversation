import assert from "node:assert/strict";
import {bindBroadcast} from "../custom_components/extended_openai_conversation_responses/frontend/overview-broadcast.js";

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
let resolveDefined;
const definedPromise = new Promise((resolve) => { resolveDefined = resolve; });
globalThis.customElements = {
  define(_name, constructor) {
    definedPanel = constructor;
    resolveDefined();
  },
  get() { return definedPanel; },
  whenDefined() { return definedPanel ? Promise.resolve() : definedPromise; },
};

const [{ExtendedOpenAIManagementPanel}, {bindRequestRules}] = await Promise.all([
  import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js"),
  import("../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js"),
]);

// These cases isolate cached data behavior after route assets are ready.
const {
  AGENT_KEY,
  ENTRY_KEY,
  applyRequestRuleSearch,
  requestRuleSearchText,
  routeAssetPromise,
  routeFeaturesReady,
} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-route.js");
await routeAssetPromise("assistant/basics");

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
  assert.equal(panel._busy, false);
  assert.ok(panel.renderStates.length <= 1, "cached load does not churn renders");

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
  assert.ok(panel.renderStates.includes(true), "uncached load exposes a busy state");
  assert.equal(panel.renderStates.at(-1), false, "uncached load settles idle");
}

{
  const panel = panelFor("capabilities", "request-rules");
  let resolveAgents;
  const calls = [];
  panel._hass = {callWS: (message) => {
    calls.push(message);
    if (message.action === "agents") {
      return new Promise((resolve) => { resolveAgents = resolve; });
    }
    return Promise.resolve({});
  }};
  panel._loadSection = async () => {};
  const loading = panel._loadAgents("agent-a");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(routeFeaturesReady("capabilities/request-rules"), true,
    "deep-link route asset starts before agents resolves");
  assert.deepEqual(calls.map((call) => call.action), ["agents"]);
  resolveAgents({agents, scopes:initialScopes, is_admin:true});
  await loading;
}

{
  const panel = panelFor("assistant", "basics");
  const listeners = new Map();
  panel.shadowRoot = {
    __eocRouteAssetWarmupBound:false,
    addEventListener(name, callback) { listeners.set(name, callback); },
  };
  panel._bindRouteAssetWarmup();
  panel._bindRouteAssetWarmup();
  assert.deepEqual([...listeners.keys()].sort(), ["focusin", "pointerdown", "pointerout", "pointerover"]);

  const target = {
    dataset:{page:"guide"},
    closest() { return this; },
  };
  listeners.get("pointerdown")({target});
  await routeAssetPromise("guide");
  assert.equal(routeFeaturesReady("guide"), true,
    "navigation intent warms the target asset");
}

{
  const panel = panelFor("capabilities", "functions");
  const calls = [];
  let resolveConfig;
  panel._hass = {callWS: (message) => {
    calls.push(message);
    if (message.section === "configuration" && message.action === "get") {
      return new Promise((resolve) => { resolveConfig = resolve; });
    }
    return Promise.resolve({});
  }};
  const loading = panel._loadSection();
  assert.equal(
    calls.filter((call) => call.section === "configuration" && call.action === "get").length,
    1,
    "Functions configuration starts before its lazy UI module resolves",
  );
  resolveConfig({title:"A", config:{}, revision:"r1"});
  await loading;
}

{
  const panel = panelFor("data-memory", "conversations");
  const calls = [];
  let resolveConfig;
  let resolveScopes;
  panel._hass = {callWS: (message) => {
    calls.push(message);
    if (message.section === "configuration" && message.action === "get") {
      return new Promise((resolve) => { resolveConfig = resolve; });
    }
    if (message.section === "scopes" && message.action === "catalog") {
      return new Promise((resolve) => { resolveScopes = resolve; });
    }
    if (message.section === "conversations" && message.action === "list") {
      return Promise.resolve({sessions:[]});
    }
    if (message.section === "conversations" && message.action === "settings") {
      return Promise.resolve({});
    }
    if (message.section === "conversations" && message.action === "active") {
      return Promise.resolve({active:[]});
    }
    return Promise.resolve({});
  }};
  const loading = panel._loadSection();
  assert.deepEqual(
    calls.map((call) => [call.section, call.action]),
    [["configuration", "get"], ["scopes", "catalog"], ["conversations", "list"], ["conversations", "active"]],
    "History primary and secondary requests start together for a known scope",
  );
  assert.equal(calls.find((call) => call.section === "scopes")?.scope_kind, "archive");
  resolveConfig({title:"A", config:{}, revision:"r1"});
  resolveScopes({scopes:initialScopes});
  await loading;
  assert.equal(panel._busy, false);
  assert.ok(panel._contentData?.sessions);
}

{
  globalThis.localStorage.values.clear();
  globalThis.localStorage.setItem(AGENT_KEY, "agent-a");
  globalThis.localStorage.setItem(ENTRY_KEY, "entry-a");
  const panel = panelFor("overview", null);
  const calls = [];
  const resolvers = new Map();
  panel._hass = {callWS: (message) => {
    calls.push(message);
    return new Promise((resolve) => {
      const key = message.action === "summary"
        ? "summary"
        : message.action === "snapshot"
          ? "snapshot"
          : message.action;
      resolvers.set(key, resolve);
    });
  }};
  const loading = panel._loadAgents();

  for (const action of ["summary", "snapshot", "agents"]) {
    assert.equal(
      calls.filter((call) => call.action === action).length,
      1,
      `${action} starts before agents resolves`,
    );
  }

  resolvers.get("agents")({agents, scopes:initialScopes, is_admin:true});
  resolvers.get("summary")({
    agent:{...agents[0], guest_mode:{}},
    usage:{today:{}, month:{}},
    conversations:{},
    load_errors:[],
  });
  resolvers.get("snapshot")({
    enabled:false,
    can_manage:true,
    catalog:{satellites:[], areas:[]},
    history:[],
  });
  await loading;
  assert.equal(panel._result?.load_errors?.length, 0);
}

{
  globalThis.localStorage.values.clear();
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
  assert.deepEqual(calls.map((call) => call.section), ["scopes", "memories", "memories", "scopes", "memories", "memories"]);
  assert.equal(calls[0].scope_kind, "memory");
  assert.equal(calls[3].scope_kind, "temporary");
  assert.deepEqual(calls.slice(4, 6).map((call) => [call.action, call.scope_id]), [
    ["temporary_list", "user:current"],
    ["temporary_list", "shared"],
  ]);

  panel._page = "guide";
  panel._subsection = null;
  await panel._loadSection();
  panel._page = "data-memory";
  panel._subsection = "memories";
  await panel._loadSection();
  assert.deepEqual(calls.slice(6).map((call) => call.section), ["memories"]);

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

  panel._scopeCatalogCache.set("agent-a|scopes|memory", initialScopes);
  panel._scopeCatalogCache.set("agent-b|scopes|memory", initialScopes);
  panel._scopeCatalogVisitKey = "agent-a|scopes|memory";
  panel._invalidateAfterMutation("agent-a", "memories", "delete");
  assert.equal(panel._scopeCatalogCache.has("agent-a|scopes|memory"), false);
  assert.equal(panel._scopeCatalogCache.has("agent-b|scopes|memory"), true);
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
  assert.equal(calls.filter((call) => call.section === "scopes").length, 1);
}

{
  const panel = panelFor("capabilities", "request-rules");
  let resolveRules;
  panel._hass = {callWS: () => new Promise((resolve) => { resolveRules = resolve; })};
  const oldLoad = panel._loadSection();
  panel._page = "data-memory";
  panel._subsection = "knowledge";
  const knowledge = {sources:[{source_id:"current"}]};
  const knowledgeKey = "agent-a|data-memory/knowledge";
  panel._sectionCache.set(knowledgeKey, knowledge);
  panel._eocSectionCacheTimes.set(knowledgeKey, Date.now());
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
  assert.equal(shadowRootListeners, 0, "Request Rule collection ownership should not add a persistent root listener");
}

{
  const panel = panelFor("usage-maintenance", "usage");
  const started = [];
  const resolvers = new Map();
  panel._hass = {callWS: (message) => {
    started.push(message.action);
    return new Promise((resolve) => resolvers.set(message.action, resolve));
  }};
  const loading = panel._loadSection();

  for (const action of ["summary", "runs", "retention"]) {
    assert.ok(
      started.includes(action),
      `${action} starts before the lazy Usage UI module resolves`,
    );
  }
  assert.equal(
    routeFeaturesReady("usage-maintenance/usage"),
    false,
    "Usage requests start while the chart feature is still cold",
  );
  await routeAssetPromise("usage-maintenance/usage");
  assert.ok(started.includes("daily"), "daily starts once its lazy usage-data helper resolves");

  resolvers.get("summary")?.({});
  resolvers.get("daily")?.({days:[]});
  resolvers.get("runs")?.({runs:[]});
  resolvers.get("retention")?.({});
  await loading;
  assert.equal(routeFeaturesReady("usage-maintenance/usage"), true);
}

{
  assert.equal(requestRuleSearchText({name:"Good Night",phrases:["Bed Time"],action_type:"local_action"}), "good night bed time local_action");
  let listQueries = 0;
  const firstCard = {dataset:{ruleKey:"one"}, hidden:false};
  const secondCard = {dataset:{ruleKey:"two"}, hidden:false};
  const empty = {hidden:true};
  const count = {textContent:""};
  const search = {value:"night"};
  const list = {
    querySelectorAll(selector) {
      assert.equal(selector, "[data-rule-key]");
      listQueries += 1;
      return [firstCard, secondCard];
    },
    querySelector(selector) {
      return selector === "[data-eoc-rule-search-empty]" ? empty : null;
    },
    append() {},
  };
  const root = {
    querySelector(selector) {
      if (selector === "#rule-search") return search;
      if (selector === ".rule-list") return list;
      if (selector === ".search-row .count") return count;
      return null;
    },
    ownerDocument:{createElement:() => null},
  };
  const panel = {
    _viewKey:() => "capabilities/request-rules",
    _query:"",
    _result:{rules:[
      {id:"one",name:"Good Night",phrases:["Bed Time"],action_type:"local_action"},
      {id:"two",name:"Think Carefully",phrases:["reason"],action_type:"model_routing"},
    ]},
  };

  assert.equal(applyRequestRuleSearch(panel, root), 1);
  assert.equal(firstCard.hidden, false);
  assert.equal(secondCard.hidden, true);
  assert.equal(listQueries, 1);

  search.value = "think";
  assert.equal(applyRequestRuleSearch(panel, root), 1);
  assert.equal(firstCard.hidden, true);
  assert.equal(secondCard.hidden, false);
  assert.equal(listQueries, 1, "keystrokes reuse cached cards and normalized search text");

  panel._eocRequestRuleCollectionRevision = 1;
  applyRequestRuleSearch(panel, root);
  assert.equal(listQueries, 2, "collection changes rebuild the search representation");
}

// A large delivery history must resolve satellite names with bounded work.
// Reading each satellite ID for every delivery would make this quadratic.
{
  const size = 300;
  let idReads = 0;
  const satellites = Array.from({length:size}, (_, index) => ({
    get id() { idReads++; return `satellite-${index}`; },
    name:`Satellite ${index}`,
  }));
  const host = {innerHTML:""};
  const panel = {
    _viewKey:() => "overview",
    _e:String,
    shadowRoot:{querySelector:(selector) => selector === "#broadcast-card" ? host : null, querySelectorAll:() => []},
  };
  const deliveries = Object.fromEntries(satellites.map((_, index) => [`satellite-${index}`, {status:"delivered"}]));
  await bindBroadcast(panel, Promise.resolve({
    enabled:false,
    can_manage:false,
    catalog:{satellites, areas:[]},
    history:[{message:"Update", created_at:"2026-01-01T00:00:00Z", deliveries}],
  }));
  assert.match(host.innerHTML, /Satellite 299/);
  assert.ok(idReads <= size * 3, `broadcast history performed ${idReads} satellite ID reads for ${size} deliveries`);
}
// Exercise cache ownership through the actual host, with no performance installer.
{
  const panel = panelFor("capabilities", "request-rules");
  const key = panel._sectionCacheKey();
  panel._sectionCache.set(key, {rules:["stale"]});
  let loads = 0;
  panel._hass = {callWS: async () => ({rules:[++loads]})};
  await panel._loadSection();
  assert.equal(loads, 1, "cache without timestamp refreshes");
  const fetchedAt = panel._eocSectionCacheTimes.get(key);
  await panel._loadSection();
  assert.equal(loads, 1);
  assert.equal(panel._eocSectionCacheTimes.get(key), fetchedAt, "hits do not slide TTL");
  panel._eocSectionCacheTimes.set(key, Date.now() - 31_000);
  await panel._loadSection();
  assert.equal(loads, 2);
}

{
  const panel = panelFor("data-memory", "conversations");
  panel._contentData = {sessions:{sessions:[{session_id:"one"}], returned:1, total:1}};
  panel._confirm = async () => true;
  panel._toast = () => {};
  let resolveDelete;
  panel._call = () => new Promise((resolve) => { resolveDelete = resolve; });
  const deleting = panel._deleteSession("one");
  await Promise.resolve();
  panel._scopeId = "user:other";
  resolveDelete({deleted_sessions:1});
  await deleting;
  assert.equal(panel._contentData.sessions.sessions.length, 1,
    "late deletion cannot patch a different History scope");
}
