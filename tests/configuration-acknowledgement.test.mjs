import assert from "node:assert/strict";
import test from "node:test";
import {captureConfigurationSubmission, reconcileConfigurationAcknowledgement, completeConfigurationAcknowledgement} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
import {saveConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/management-actions.js";
import {bindKnowledgeAvailability} from "../custom_components/extended_openai_conversation_responses/frontend/management-knowledge-feature.js";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {mergeAcknowledgedDraft} from "../custom_components/extended_openai_conversation_responses/frontend/unsaved-state.js";

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function panel() {
  const host = {
    _agentId:"a", _draftAgentId:"a", entryId:"entry-a", view:"assistant/model-responses",
    _configData:{revision:"r1", title:"Saved title", config:{prompt:"Saved prompt", max_tokens:100, knowledge_enabled:false}},
    _draftTitle:"Edited title", _loadToken:1, toasts:[], calls:[], renders:0,
    shadowRoot:{querySelector:() => null, querySelectorAll:() => []},
    _selectedAgent() { return {entry_id:this.entryId}; },
    _viewKey() { return this.view; },
    _rememberCleanConfiguration(data) { this.clean = structuredClone(data); },
    _syncConfigDirty() { this.dirty = JSON.stringify(this._draft) !== JSON.stringify(this._configData.config); },
    _setSaving() {}, _render() { this.renders++; },
    _toast(...args) { this.toasts.push(args); },
  };
  host._draft = {...host._configData.config, prompt:"Submitted prompt", max_tokens:"200"};
  host._result = host._configData;
  host._eocDirtyConfigKeys = new Set(["prompt", "max_tokens", "__title"]);
  return host;
}

for (const full of [false, true]) {
  test(`queued ${full ? "full" : "field"} acknowledgement preserves a local revert after an earlier acknowledgement`, async () => {
    const host = panel();
    host._configData.config.functions = ["first", "second"];
    host._draft.functions = ["first", "second"];
    const first = captureConfigurationSubmission(host), second = captureConfigurationSubmission(host);
    const projection = full ? {} : {fields:["functions"]};
    const saved = (revision, functions) => ({valid:true, revision, title:host._draftTitle,
      config:full ? {...structuredClone(host._configData.config), functions} : {functions}});
    reconcileConfigurationAcknowledgement(host, saved("r2", ["second"]), first, projection);
    assert.deepEqual(host._draft.functions, ["second"]);
    host._draft.functions = ["first", "second"];
    reconcileConfigurationAcknowledgement(host, saved("r3", []), second, projection);
    assert.deepEqual(host._draft.functions, ["first", "second"]);
    assert.deepEqual(host._configData.config.functions, []);
    assert.equal(host._configData.revision, "r3");
    assert.equal(host.dirty, true);
    host._syncConfigDirty = () => { host._eocDirtyConfigKeys = new Set(["functions"]); };
    host._call = async (_section, _action, payload) => {
      assert.equal(payload.revision, "r3");
      assert.deepEqual(payload.config.functions, ["first", "second"]);
      return {valid:true, revision:"r4", title:host._draftTitle, config:structuredClone(host._draft)};
    };
    await saveConfiguration(host, {});
    assert.deepEqual(host._configData.config.functions, ["first", "second"]);
    assert.equal(host._configData.revision, "r4");
  });
}

test("full acknowledgement canonicalizes unchanged fields and preserves newer field/title edits", async () => {
  const host = panel(), gate = deferred();
  host._call = async (...args) => { host.calls.push(args); return gate.promise; };
  const pending = saveConfiguration(host, {});
  host._draft.prompt = "Newer prompt";
  host._draftTitle = "Newer title";
  gate.resolve({valid:true, revision:"r2", title:"Edited title", config:{prompt:"Submitted prompt", max_tokens:200, knowledge_enabled:false}});
  await pending;
  assert.equal(host._draft.prompt, "Newer prompt");
  assert.equal(host._draft.max_tokens, 200);
  assert.equal(host._draftTitle, "Newer title");
  assert.equal(host._configData.config.prompt, "Submitted prompt");
  assert.equal(host._configData.revision, "r2");
  assert.equal(host.clean.revision, "r2");
  assert.equal(host._result, host._configData);
  assert.equal(host.dirty, true);
  assert.equal(host.calls.length, 1);
});

for (const change of ["agent", "entry"]) {
  test(`a late acknowledgement cannot update a different ${change}`, async () => {
    const host = panel(), gate = deferred();
    host._call = () => gate.promise;
    const pending = saveConfiguration(host, {});
    if (change === "agent") host._agentId = "b";
    else host.entryId = "entry-b";
    host._configData = {revision:"other", config:{prompt:"Other prompt"}};
    host._result = host._configData;
    const other = structuredClone(host._configData), draft = structuredClone(host._draft);
    gate.resolve({valid:true, revision:"r2", config:{prompt:"Submitted prompt"}});
    await pending;
    assert.deepEqual(host._configData, other);
    assert.deepEqual(host._draft, draft);
    assert.equal(host.toasts.length, 0);
  });
}

test("render failure after a full save preserves the committed baseline and reports a refresh warning", async () => {
  const host = panel();
  host._call = async () => ({valid:true, revision:"r2", title:"Edited title", config:structuredClone(host._draft)});
  host._render = () => { throw new Error("Renderer unavailable"); };
  await saveConfiguration(host, {});
  assert.equal(host._configData.revision, "r2");
  assert.match(host.toasts.at(-1)[0], /^Changes saved, but .*Renderer unavailable/);
  assert.ok(!host.toasts.some(([message]) => message.startsWith("Unable to save")));
  assert.equal(host._configurationSaving, false);
});

for (const outcome of ["invalid", "lost response"]) {
  test(`${outcome} does not adopt an unacknowledged baseline or replay the mutation`, async () => {
    const host = panel(), before = structuredClone(host._configData);
    let calls = 0;
    host._call = async () => {
      calls++;
      if (outcome === "lost response") throw new Error("Connection closed after submission");
      return {valid:false, errors:{}};
    };
    await saveConfiguration(host, {});
    assert.deepEqual(host._configData, before);
    assert.equal(host._draft.prompt, "Submitted prompt");
    assert.equal(calls, 1);
    assert.equal(host._configurationSaving, false);
  });
}

test("independent fields preserve newer same-field edits, unrelated drafts and unsaved title", () => {
  const host = panel(), submission = captureConfigurationSubmission(host);
  host._draft.knowledge_enabled = true;
  reconcileConfigurationAcknowledgement(host, {revision:"r2", config:{knowledge_enabled:false, prompt:"Unrelated server prompt"}}, submission, {fields:["knowledge_enabled"]});
  assert.equal(host._draft.knowledge_enabled, true);
  assert.equal(host._configData.config.knowledge_enabled, false);
  assert.equal(host._configData.config.prompt, "Saved prompt");
  assert.equal(host._draft.prompt, "Submitted prompt");
  assert.equal(host._draftTitle, "Edited title");
  assert.equal(host.clean, undefined, "a subset is not a complete clean snapshot");
  assert.equal(host._configDataStale, true);
});

test("an omitted revision in a legacy subset acknowledgement cannot erase the existing revision", () => {
  const host = panel();
  reconcileConfigurationAcknowledgement(host, {config:{knowledge_enabled:true}}, captureConfigurationSubmission(host), {fields:["knowledge_enabled"]});
  assert.equal(host._configData.revision, "r1");
});

test("queued acknowledgements are not mistaken for newer local edits", () => {
  const host = panel();
  host._configData.config.functions = [{name:"first"}, {name:"second"}];
  host._draft.functions = structuredClone(host._configData.config.functions);
  const first = captureConfigurationSubmission(host), second = captureConfigurationSubmission(host);
  reconcileConfigurationAcknowledgement(host, {revision:"r2", config:{functions:[{name:"second"}]}}, first, {fields:["functions"]});
  reconcileConfigurationAcknowledgement(host, {revision:"r3", config:{functions:[]}}, second, {fields:["functions"]});
  assert.deepEqual(host._draft.functions, []);
  assert.equal(host._draft.prompt, "Submitted prompt");
  assert.equal(host._configData.revision, "r3");
});

test("a local revert to the original baseline during a pending save remains an edit", () => {
  const host = panel(), submission = captureConfigurationSubmission(host);
  host._draft.prompt = "Saved prompt";
  reconcileConfigurationAcknowledgement(host, {revision:"r2", title:"Edited title", config:{...submission.draft, max_tokens:200}}, submission);
  assert.equal(host._draft.prompt, "Saved prompt");
  assert.equal(host._configData.config.prompt, "Submitted prompt");
});

test("field merging preserves presence, explicit null/zero/false, and new draft keys", () => {
  const submitted = {removed:1, explicit:null, count:2, enabled:true};
  const current = {...submitted, added:"New edit"};
  const saved = {explicit:null, count:0, enabled:false};
  assert.deepEqual(mergeAcknowledgedDraft(current, submitted, saved), {...saved, added:"New edit"});
  delete current.explicit;
  assert.ok(!Object.hasOwn(mergeAcknowledgedDraft(current, submitted, saved), "explicit"));
  assert.deepEqual(submitted, {removed:1, explicit:null, count:2, enabled:true});
});

test("secondary failure or owner replacement cannot erase acknowledgement or toast another assistant", async () => {
  for (const replace of [false, true]) {
    const host = panel(), submission = captureConfigurationSubmission(host);
    await completeConfigurationAcknowledgement(host, {revision:"r2", config:{knowledge_enabled:true}}, submission, {
      fields:["knowledge_enabled"], message:"Knowledge enabled",
      refresh:async () => { if (replace) host._agentId = "b"; throw new Error("Refresh offline"); },
    });
    assert.equal(host._configData.revision, "r2");
    assert.equal(host.toasts.length, replace ? 0 : 1);
    if (!replace) assert.match(host.toasts[0][0], /^Knowledge enabled, but /);
  }
});

test("Knowledge acknowledgement follows its assistant across routes without overwriting the new page", async () => {
  const host = panel(), gate = deferred();
  host.view = "data-memory/knowledge";
  let change;
  const input = {checked:true, addEventListener:(_type, callback) => { change = callback; }};
  host.shadowRoot.querySelector = () => input;
  host._call = () => gate.promise;
  bindKnowledgeAvailability(host);
  const pending = change();
  host.view = "capabilities/guest-mode";
  host._loadToken++;
  const page = host._result = {config:{guest_policy:true}};
  gate.resolve({revision:"r2", knowledge_enabled:true});
  await pending;
  assert.equal(host._configData.revision, "r2");
  assert.equal(host._draft.knowledge_enabled, true);
  assert.equal(host._result, page);
  assert.equal(input.disabled, false);
});

test("Guest acknowledgement uses the shared baseline while the scoped draft keeps newer edits", async () => {
  const host = panel(), gate = deferred();
  host.view = "capabilities/guest-mode";
  host._result = {config:{guest_web_search:false, count:0}, revision:"r1"};
  host._call = () => gate.promise;
  initializePageDraft(host);
  const scope = currentPageScope(host);
  scope.read().guest_web_search = true;
  const pending = scope.save();
  scope.read().count = 2;
  host._draft.prompt = "Newer unrelated prompt";
  gate.resolve({config:{guest_web_search:true, count:1}, revision:"r2"});
  await pending;
  assert.equal(scope.read().guest_web_search, true);
  assert.equal(scope.read().count, 2);
  assert.equal(scope.baseline.count, 1);
  assert.equal(host._configData.revision, "r2");
  assert.equal(host._draft.prompt, "Newer unrelated prompt");
  assert.equal(host._draft.guest_web_search, true);
});

test("a late Request Rules settings acknowledgement cannot replace another assistant's page", async () => {
  const host = panel(), gate = deferred();
  host.view = "capabilities/request-rules";
  host._result = {defaults:{fuzzy:false}, wording_groups:[], revision:"rules-a"};
  host._call = () => gate.promise;
  initializePageDraft(host);
  const scope = currentPageScope(host);
  scope.read().defaults.fuzzy = true;
  const pending = scope.save();
  host._agentId = "b";
  const other = host._result = {defaults:{fuzzy:false}, wording_groups:[], revision:"rules-b"};
  host._rulesSettingsDraft = {defaults:{fuzzy:false}, wording_groups:[]};
  gate.resolve({defaults:{fuzzy:true}, wording_groups:[], revision:"rules-a2"});
  await pending;
  assert.equal(host._result, other);
  assert.equal(host._rulesSettingsDraft.defaults.fuzzy, false);
  assert.equal(scope.revision, "rules-a2", "the original scope still owns its acknowledgement");
});
