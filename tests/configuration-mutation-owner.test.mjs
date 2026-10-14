import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = {location:{pathname:"/extended-openai/capabilities/functions"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};
const {ExtendedOpenAIManagementPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
const {captureConfigurationSubmission} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js");

function panel() {
  const host = new ExtendedOpenAIManagementPanel();
  host._agentId = "a";
  host._data = {agents:[{entry_id:"entry-a", subentry_id:"a"}, {entry_id:"entry-b", subentry_id:"b"}]};
  host._configData = {revision:"a1", config:{functions:[]}};
  host._error = null;
  return host;
}

test("a queued write cannot resolve its implicit destination after the assistant changes", async () => {
  const host = panel();
  let release, calls = 0;
  host._eocFunctionMutationTail = new Promise(resolve => { release = resolve; });
  host._callCore = async () => { calls++; return {revision:"a2"}; };
  const pending = host._call("tools", "delete", {name:"a_tool"});
  host._agentId = "b";
  host._configData = {revision:"b1", config:{functions:[]}};
  release();
  await assert.rejects(pending, /assistant changed/);
  assert.equal(calls, 0);
  assert.equal(host._configData.revision, "b1");
  assert.equal(host._pendingMutations.size, 0);
});

for (const change of ["agent", "entry"]) {
  test(`a completed Function request cannot advance another ${change}'s revision`, async () => {
    const host = panel();
    let release;
    host._callCore = () => new Promise(resolve => { release = resolve; });
    const pending = host._call("tools", "delete", {name:"a_tool"});
    while (!release) await Promise.resolve();
    if (change === "agent") host._agentId = "b";
    else host._data.agents[0].entry_id = "replacement-entry";
    host._configData = {revision:"replacement", config:{functions:[]}};
    release({revision:"a2", functions:[]});
    assert.equal((await pending).revision, "a2", "keep the acknowledgement available to its original caller");
    assert.equal(host._configData.revision, "replacement");
    assert.equal(host._eocAgentMutations, 0);
  });
}

for (const fail of [false, true]) {
  test(`a post-save catalogue ${fail ? "failure" : "response"} cannot restore the previous assistant`, async () => {
    const host = panel(), submission = captureConfigurationSubmission(host);
    host._page = "guide";
    let resolve, reject, loads = 0;
    host._hass = {callWS:() => new Promise((yes, no) => { resolve = yes; reject = no; })};
    host._loadSection = async () => { loads++; };
    host._applyScopes = () => {};
    host._render = () => {};
    const pending = host._loadAgents("a", submission);
    host._agentId = "b";
    const current = host._data = {agents:[{entry_id:"entry-b", subentry_id:"b"}]};
    if (fail) reject(new Error("Old refresh unavailable"));
    else resolve({agents:[{entry_id:"entry-a", subentry_id:"a"}]});
    await pending;
    assert.equal(host._agentId, "b");
    assert.equal(host._data, current);
    assert.equal(loads, 0);
    assert.equal(host._error, null);
  });
}
