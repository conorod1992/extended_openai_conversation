import assert from "node:assert/strict";
import {test} from "node:test";
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {}; } hasAttribute() { return false; } };
globalThis.customElements = {get() {return true;}};
const {ExtendedOpenAIDebugPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/debug-panel.js");
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a; reject=b;}); return {promise,resolve,reject};};
function panel() {
  const host = new ExtendedOpenAIDebugPanel();
  host._render = () => {};
  host._toast = () => {};
  host._agents = [{entry_id:"parent",subentry_id:"one"},{entry_id:"parent",subentry_id:"two"}];
  host._agent = host._agents[0];
  return host;
}
test("Debug discards stale successes and failures across refresh and assistant selection", async () => {
  const host = panel(), pending = [];
  host._hass = {callWS(message) {const held=deferred();pending.push({message,...held});return held.promise;}};
  const first = host._loadRuns(); await Promise.resolve();
  const second = host._loadRuns(); await Promise.resolve();
  pending[1].resolve({enabled:false,runs:[{debug_id:"new"}]});await second;
  pending[0].resolve({enabled:true,runs:[{debug_id:"old"}]});await first;
  assert.equal(host._runs[0].debug_id,"new");
  const stale = host._loadRuns(); await Promise.resolve();
  const selected = host._selectAgent("two");await Promise.resolve();
  pending[3].resolve({enabled:false,runs:[{debug_id:"two"}]});await selected;
  pending[2].reject(new Error("old failure"));await stale;
  assert.equal(host._runs[0].debug_id,"two");assert.equal(host._error,"");
  assert.equal(pending[3].message.subentry_id,"two");
});
test("Debug serializes mutations, invalidates reads, and recovers after failure", async () => {
  const host=panel(), held=deferred(), events=[];
  host._hass={async callWS(message) {events.push(message);if(message.action==="configure"&&message.enabled) return held.promise;if(message.action==="runs")return {enabled:false,runs:[]};return {enabled:false};}};
  const first=host._configure({enabled:true});await Promise.resolve();
  const second=host._configure({enabled:false});await Promise.resolve();
  assert.equal(events.length,1);
  held.reject(new Error("controlled failure"));await Promise.all([first,second]);
  assert.deepEqual(events.map(item=>item.action),["configure","configure","runs"]);
  assert.equal(host._status.enabled,false);
  await host._configure({limit:25});assert.equal(events.at(-2).limit,25);
});
test("Debug clear confirmation remains bound to its assistant and cancellation is inert", async () => {
  const host=panel(), confirmation=deferred(), calls=[];
  host._hass={async callWS(message){calls.push(message);return {runs:[]};}};
  host._confirmClear=()=>confirmation.promise;
  const clear=host._clear();await host._selectAgent("two");confirmation.resolve(true);await clear;
  assert.ok(!calls.some(item=>item.action==="clear"));
  host._confirmClear=async()=>false;await host._clear();assert.ok(!calls.some(item=>item.action==="clear"));
});
test("Debug optional storage access and quota failure preserve backend loading", async () => {
  const host=panel();let reads=0;
  Object.defineProperty(globalThis,"localStorage",{configurable:true,get(){throw new Error("blocked");}});
  host._hass={async callWS(message){if(message.action==="agents")return {agents:host._agents};reads++;return {runs:[],enabled:true};}};
  await host._loadAgents();assert.equal(reads,1);assert.equal(host._status.enabled,true);
  Object.defineProperty(globalThis,"localStorage",{configurable:true,value:{getItem:()=>"two",setItem(){throw new Error("quota");}}});
  await host._loadAgents();assert.equal(host._agent.subentry_id,"two");assert.equal(reads,2);
  delete globalThis.localStorage;
});
