import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(`../custom_components/extended_openai_conversation_responses/frontend/${name}`, import.meta.url);
const elements = new Map();
globalThis.window = {location:{pathname:"/extended-openai/guide"}, addEventListener(){}, removeEventListener(){}};
globalThis.localStorage = {getItem(){return "agent-b";},setItem(){}};
globalThis.customElements = {
  define(name, type) { assert.equal(elements.has(name), false); elements.set(name, type); },
  get(name) { return elements.get(name); },
  whenDefined(name) { assert.ok(elements.has(name)); return Promise.resolve(); },
};
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = {innerHTML:"",hasChildNodes:()=>false,querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},dispatchEvent(){}}; }
  hasAttribute(name) { return name === "embedded" && this.embedded === true; }
};

const {ExtendedOpenAIManagementPanel: Panel} = await import(frontend("management-panel.js"));
const {routeAssetPromise, getRouteFeature, getConfigurationTools} = await import(frontend("management-route.js"));
Object.freeze(Panel.prototype);
const panelMethods = Object.getOwnPropertyDescriptors(Panel.prototype);
const loaderOwner = {get constructor() { throw new Error("Lazy loaders must not inspect or patch a panel constructor"); }};
for (const view of [
  "capabilities/quiet-hours", "capabilities/functions", "data-memory/conversations",
  "usage-maintenance/usage", "usage-maintenance/request-debug", "usage-maintenance/diagnostics",
  "assistant/voice", "data-memory/memory-settings",
]) {
  await routeAssetPromise(view, loaderOwner);
  assert.ok(getRouteFeature(view), `${view} should publish its helper module`);
  await routeAssetPromise(view, loaderOwner);
}
assert.deepEqual(Object.getOwnPropertyDescriptors(Panel.prototype), panelMethods);

await routeAssetPromise("capabilities/functions", loaderOwner);
assert.ok(getConfigurationTools(), "Functions should publish its dedicated tools editor");
assert.equal(typeof getConfigurationTools().renderTools, "function");
assert.equal(typeof getRouteFeature("agent-config")?.renderTools, "undefined",
  "ordinary configuration entry must not retain Function Tools exports");

const debugHelpers = getRouteFeature("usage-maintenance/request-debug");
await debugHelpers.ensureDebugPanel();
const Debug = elements.get("extended-openai-debug-panel");
Object.freeze(Debug.prototype);
const debugMethods = Object.getOwnPropertyDescriptors(Debug.prototype);
await debugHelpers.ensureDebugPanel();
assert.deepEqual(Object.getOwnPropertyDescriptors(Debug.prototype), debugMethods);
assert.deepEqual(Object.getOwnPropertyDescriptors(Panel.prototype), panelMethods);

// First embedded load selects the owning assistant before any run request.
const debug = new Debug();
debug.embedded = true;
debug._managementAgentId = "agent-a";
const debugCalls = [];
debug._hass = {callWS: async (message) => {
  debugCalls.push(message);
  if (message.action === "agents") return {agents:[{entry_id:"entry-a",subentry_id:"agent-a",title:"A"},{entry_id:"entry-b",subentry_id:"agent-b",title:"B"}]};
  return {runs:[]};
}};
await debug._loadAgents();
assert.deepEqual(debugCalls.map((call)=>[call.action,call.subentry_id]), [["agents",undefined],["runs","agent-a"]]);
assert.match(debug.shadowRoot.innerHTML, /Previous requests/);
assert.match(debug.shadowRoot.innerHTML, /Copy visible page/);
assert.match(debug.shadowRoot.innerHTML, /<label hidden>Conversation agent/);
await debug._getRun("debug-1",5);
assert.deepEqual(debugCalls.at(-1), {type:"extended_openai_conversation_responses/request_debug",action:"get",entry_id:"entry-a",subentry_id:"agent-a",debug_id:"debug-1",provider_offset:5,provider_limit:5});

// Standalone mode keeps the unpaged request and its original visible controls.
const standalone = new Debug();
standalone._hass = debug._hass;
standalone._render();
assert.doesNotMatch(standalone.shadowRoot.innerHTML, /id="debug-provider-next"/);
assert.match(standalone.shadowRoot.innerHTML, /Copy entire debug log/);
await standalone._getRun("standalone");
assert.equal(Object.hasOwn(debugCalls.at(-1), "provider_offset"), false);

const panel = new Panel();
panel._page = "usage-maintenance";
panel._subsection = "usage";
panel._agentId = "agent-a";
panel._data = {is_admin:true,agents:[{entry_id:"entry-a",subentry_id:"agent-a"},{entry_id:"entry-b",subentry_id:"agent-b"}]};
const usageCalls = [];
panel._hass = {callWS: async (message) => {
  usageCalls.push(message);
  if (usageCalls.length === 1) {
    panel._agentId = "agent-b";
    return {days:Array.from({length:366},(_,i)=>({date:new Date(Date.UTC(2024,0,i+1)).toISOString().slice(0,10)}))};
  }
  return {days:[]};
}};
await panel._call("usage","daily");
assert.equal(usageCalls.length,1);
assert.deepEqual(usageCalls.map((call)=>[call.entry_id,call.subentry_id]), [["entry-a","agent-a"]]);
assert.match(usageCalls[0].start_date,/^\d{4}-\d{2}-\d{2}$/);
assert.match(usageCalls[0].end_date,/^\d{4}-\d{2}-\d{2}$/);
assert.match(panel._usage(), /Usage period/);
assert.doesNotMatch(panel._usage(), /Input footprint/);
assert.match(panel._dialogs(), /id="usage-request-dialog"/);

// A deleted installer cannot accidentally be revived through a route import.
const coreEditorSource = await readFile(frontend("agent-config-editor.js"),"utf8");
assert.doesNotMatch(coreEditorSource,/renderTools|reconcileTools|agent-config-native-yaml/,
  "ordinary configuration entry must stay free of Function Tools exports");
const coreBaseSource = await readFile(frontend("agent-config-editor-base.js"),"utf8");
assert.doesNotMatch(coreBaseSource,/keyed-collection|ha-llm-tools|tool-yaml-editor-adapter/,
  "ordinary configuration base must not import Function Tools dependencies");
const toolsBaseSource = await readFile(frontend("agent-config-tools-base.js"),"utf8");
assert.match(toolsBaseSource,/keyed-collection/);
assert.match(toolsBaseSource,/ha-llm-tools/);
assert.match(toolsBaseSource,/tool-yaml-editor-adapter/);
const routeSource = await readFile(frontend("management-route.js"),"utf8");
assert.doesNotMatch(routeSource,/panel\.constructor|\.install[A-Z]/);
for (const name of ["quiet-hours-ui.js","management-function-repair.js","usage-chart.js","management-provider-credentials.js","debug-management.js"]) {
  const source = await readFile(frontend(name),"utf8");
  assert.doesNotMatch(source,/prototype\.|Symbol\.for\(|export function install[A-Z]/,name);
}

// Cleanup is a panel lifecycle responsibility even after lazy activation.
let removals = 0;
const originalRemove = panel.shadowRoot.removeEventListener.bind(panel.shadowRoot);
panel.shadowRoot.removeEventListener = (type, handler, options) => {
  if (type === "eoc-diagnostics-result") removals++;
  return originalRemove(type, handler, options);
};
panel._eocProviderCredentialResultHandler = () => {};
panel.disconnectedCallback();
panel.disconnectedCallback();
assert.equal(removals,1);
assert.equal(panel._eocProviderCredentialResultHandler,null);

// Permission checks cannot depend on whether the Debug feature has been visited.
panel._data.is_admin = false;
panel._subsection = "request-debug";
assert.equal(panel._canAccessView("usage-maintenance","request-debug"),false);
assert.doesNotMatch(panel._content(panel._data.agents[0]),/<extended-openai-debug-panel/);
console.log("Lazy route ownership, frozen prototypes, pinned paging and native Debug tests passed");
