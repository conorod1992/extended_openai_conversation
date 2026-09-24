import assert from "node:assert/strict";

globalThis.window = {location:{pathname:"/extended-openai/data-memory/conversations"}, addEventListener(){}, removeEventListener(){}};
globalThis.history = {pushState(){}};
globalThis.localStorage = {getItem(){return null;}, setItem(){}};
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = {querySelector:()=>null, hasChildNodes:()=>false}; }
};
globalThis.customElements = {define(){}, get(){return null;}, whenDefined(){return Promise.resolve();}};

const frontend = (name) => new URL(`../custom_components/extended_openai_conversation_responses/frontend/${name}`, import.meta.url);
const [{ExtendedOpenAIManagementPanel: Panel}, route, {reconcileHistoryConfiguration}] = await Promise.all([
  import(frontend("management-panel.js")),
  import(frontend("management-route.js")),
  import(frontend("management-renderer.js")),
]);

assert.equal(route.routeAssetKind("data-memory/conversations"), null);
await route.routeAssetPromise("data-memory/conversations");
assert.equal(route.routeFeaturesReady("data-memory/conversations"), true);
assert.equal(route.getConfigurationEditor(), undefined);
assert.equal(route.getRouteFeature("memory-browser"), undefined);

const panel = new Panel();
panel._page = "data-memory";
panel._subsection = "conversations";
panel._agentId = "agent-a";
panel._scopeId = "user:current";
panel._data = {
  agents:[{entry_id:"entry-a", subentry_id:"agent-a", title:"A"}],
  scopes:[{scope_id:"user:current", scope_type:"user", display_name:"Current"}],
  is_admin:true,
};
panel._loadScopes = async () => {};
let resolveConfig;
const config = new Promise((resolve) => { resolveConfig = resolve; });
panel._loadConfigDraft = () => config;
panel._call = async (section, action) => {
  if (section === "conversations" && action === "list") return {sessions:[{session_id:"s1", title:"First conversation", turn_count:1, scope_source:"user"}], has_more:false};
  if (section === "conversations" && action === "active") return {active:[]};
  throw new Error(`Unexpected request ${section}/${action}`);
};
const renders = [];
panel._render = () => { if (!panel._busy) renders.push(panel._content(panel._selectedAgent())); };
let settingsPatches = 0;
panel._patchHistorySettings = () => { settingsPatches++; return true; };
await panel._loadSectionData();
assert.match(renders.at(-1), /First conversation/);
assert.match(renders.at(-1), /Loading archive settings/);
assert.equal(route.getConfigurationEditor(), undefined);

panel._configData = {config:{}, title:"A", revision:"r1"};
panel._draftAgentId = "agent-a";
resolveConfig();
await Promise.resolve();
assert.equal(settingsPatches, 1, "late configuration updates only the settings region");
assert.match(renders.at(-1), /First conversation/);

const list = {innerHTML:"First conversation"};
const settings = {innerHTML:"Loading archive settings"};
panel.shadowRoot = {querySelector(selector) {
  if (selector === "[data-eoc-history-config]") return settings;
  if (selector === "[data-eoc-history-list]") return list;
  return null;
}};
panel._dialogs = () => "";
panel._busy = false;
assert.equal(reconcileHistoryConfiguration(panel, "Archive settings ready"), true);
assert.equal(settings.innerHTML, "Archive settings ready");
assert.equal(list.innerHTML, "First conversation");

panel._contentData.load_errors = [{key:"config", message:"Configuration unavailable"}];
assert.match(panel._historySettingsMarkup(), /Archive settings unavailable: Configuration unavailable/);
assert.match(renders.at(-1), /First conversation/);
