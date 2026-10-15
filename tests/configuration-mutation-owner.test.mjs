import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = {location:{pathname:"/extended-openai/capabilities/functions"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};
const {ExtendedOpenAIManagementPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
const {captureConfigurationSubmission} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js");
const {synchronizePersistedFunctions} = await import("../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js");

function panel() {
  const host = new ExtendedOpenAIManagementPanel();
  host._agentId = "a";
  host._data = {agents:[{entry_id:"entry-a", subentry_id:"a"}, {entry_id:"entry-b", subentry_id:"b"}]};
  host._configData = {revision:"a1", config:{functions:[]}};
  host._error = null;
  return host;
}

for (const kind of ["function", "group"]) {
  test(`partial ${kind} rename acknowledgement must reread custom Guest policy on navigation`, async () => {
    const host = panel();
    host._viewKey = () => "capabilities/functions";
    host._isDraftView = () => true;
    host._draftAgentId = "a";
    host._configData = {revision:"a1", title:"Agent", config:{
      functions:[{spec:{name:"old_tool"}}], function_groups:[{id:"old_group", functions:["old_tool"]}],
      guest_function_policy:"custom", guest_allowed_function_names:["old_tool"], guest_allowed_group_ids:["old_group"],
    }};
    host._draft = structuredClone(host._configData.config);
    host._draftTitle = "Agent";
    host._rememberCleanConfiguration(host._configData);
    const submission = captureConfigurationSubmission(host);
    const persisted = structuredClone(host._configData);
    persisted.revision = "a2";
    if (kind === "function") {
      persisted.config.functions[0].spec.name = "new_tool";
      persisted.config.function_groups[0].functions = ["new_tool"];
      persisted.config.guest_allowed_function_names = ["new_tool"];
    } else {
      persisted.config.function_groups[0].id = "new_group";
      persisted.config.guest_allowed_group_ids = ["new_group"];
    }
    host._invalidateCleanConfiguration("a");
    synchronizePersistedFunctions(host, {revision:"a2", functions:persisted.config.functions, function_groups:persisted.config.function_groups}, submission);
    assert.equal(host._configDataStale, true);
    assert.equal(host._freshCleanConfiguration("a", "full"), null);
    assert.equal(host._configData.revision, "a2");
    host._viewKey = () => "capabilities/guest-mode";
    assert.equal(host._hydrateCleanConfiguration(host._viewKey()), false);
    const calls = [];
    host._call = async (section, action) => { calls.push([section, action]); return structuredClone(persisted); };
    await host._loadConfigDraft();
    assert.deepEqual(calls, [["configuration", "get"]]);
    assert.deepEqual(host._draft.guest_allowed_function_names, persisted.config.guest_allowed_function_names);
    assert.deepEqual(host._draft.guest_allowed_group_ids, persisted.config.guest_allowed_group_ids);
    assert.equal(host._configDataStale, false);
    assert.deepEqual(host._freshCleanConfiguration("a", "full").config, persisted.config);
  });
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
