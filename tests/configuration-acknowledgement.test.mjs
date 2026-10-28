import assert from "node:assert/strict";
import test from "node:test";
import {captureConfigurationSubmission, reconcileConfigurationAcknowledgement, completeConfigurationAcknowledgement, configurationDraftBaseline, mergeConfigurationRead, discardConfigurationDraft} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
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

for (const snapshot of ["healthy", "still needs repair", "partial"]) {
  test(`a ${snapshot} agent acknowledgement updates repair status without a catalogue reload`, () => {
    const host = panel();
    const issue = {repairable:true, field:"functions", message:"Legacy Function"};
    const selected = {entry_id:"entry-a", subentry_id:"a", configuration_issue:issue};
    host._selectedAgent = () => selected;
    const agent = snapshot === "partial" ? {function_count:2}
      : {entry_id:"entry-a", subentry_id:"a", ...(snapshot === "still needs repair" ? {configuration_issue:issue} : {})};
    reconcileConfigurationAcknowledgement(host, {config:{functions:[]}, agent}, captureConfigurationSubmission(host), {fields:["functions"]});
    assert.equal(selected.configuration_issue, snapshot === "healthy" ? undefined : issue);
  });
}

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

for (const full of [false, true]) {
  test(`an overtaken ${full ? "full" : "partial"} acknowledgement keeps the newer read until revalidation`, async () => {
    const host = panel(), submission = captureConfigurationSubmission(host), read = deferred();
    const current = host._configData = {revision:"r3", title:"Newer title", config:{prompt:"Newer persisted prompt", max_tokens:300}};
    host._result = {sources:["A different page"]};
    const otherPage = host._result;
    host._eocConfigurationReadEpoch = 1;
    host._draft = {...current.config, prompt:"Still unsaved"};
    let refreshed = false, reads = 0;
    host._loadConfigDraft = async options => {
      reads++;
      assert.deepEqual(options, {preserveResult:true});
      assert.equal(refreshed, true, "finish editor acknowledgement before a new asynchronous boundary");
      assert.equal(host._configData, current, "never publish the stale r2 projection, even briefly");
      await read.promise;
    };
    const pending = completeConfigurationAcknowledgement(host,
      {revision:"r2", title:"Submitted title", config:{prompt:"Submitted prompt", max_tokens:200}}, submission,
      {message:"Saved", ...(full ? {} : {fields:["max_tokens"]}), refresh:() => {refreshed = true;}});
    await Promise.resolve();
    assert.equal(refreshed, true);
    assert.equal(reads, 1);
    assert.equal(host._configData, current);
    assert.equal(host._configDataStale, true);
    assert.equal(host._draft.prompt, "Still unsaved");
    read.resolve();
    await pending;
    assert.equal(host._result, otherPage);
    assert.deepEqual(host.toasts.map(([message, warning = false]) => [message, warning]), [["Saved", false]]);
  });
}

test("a failed revalidation keeps the completed write and editor acknowledgement without replay", async () => {
  const host = panel(), submission = captureConfigurationSubmission(host);
  host._eocConfigurationReadEpoch = 1;
  let acknowledged = 0, reads = 0;
  host._loadConfigDraft = async () => { reads++; throw new Error("Fresh read offline"); };
  await completeConfigurationAcknowledgement(host, {revision:"r2", config:submission.draft}, submission, {
    message:"Saved", refresh:() => { acknowledged++; },
  });
  assert.equal(acknowledged, 1);
  assert.equal(reads, 1);
  assert.equal(host._configData.revision, "r1");
  assert.equal(host._configDataStale, true);
  assert.match(host.toasts.at(-1)[0], /^Saved, but .*Fresh read offline/);
});

for (const change of ["agent", "entry", "visit", "disconnect"]) {
  test(`acknowledgement revalidation cannot render after ${change} replacement`, async () => {
    const host = panel(), submission = captureConfigurationSubmission(host), read = deferred();
    host._eocConfigurationReadEpoch = 1;
    host._result = host._configData;
    host._loadConfigDraft = () => read.promise;
    const pending = completeConfigurationAcknowledgement(host, {revision:"r2", config:submission.draft}, submission,
      {message:"Saved", refresh:() => {}});
    await Promise.resolve();
    if (change === "agent") host._agentId = "b";
    if (change === "entry") host.entryId = "replacement";
    if (change === "visit") host._loadToken++;
    if (change === "disconnect") host.isConnected = false;
    read.resolve(); await pending;
    assert.equal(host.renders, 0);
  });
}

test("pending full-save fields retain original-value reversions across a precommit read", async () => {
  const host = panel(), gate = deferred();
  host._call = () => gate.promise;
  const saving = saveConfiguration(host, {});
  host._draft.prompt = "Saved prompt";
  host._draftTitle = "Saved title";
  const read = {revision:"r1", title:"Read title", config:{...host._configData.config, knowledge_enabled:true}};
  const merged = mergeConfigurationRead(host, {draft:host._draft, title:host._draftTitle}, host._configData, read);
  assert.equal(merged.draft.prompt, "Saved prompt");
  assert.equal(merged.draft.max_tokens, "200");
  assert.equal(merged.title, "Saved title");
  assert.equal(merged.draft.knowledge_enabled, true, "unrelated fields still follow the accepted read");
  host._draft = merged.draft;
  host._draftTitle = merged.title;
  host._configData = read;
  host._eocConfigurationReadEpoch = 1;
  gate.resolve({valid:true, revision:"r2", title:"Edited title", config:{prompt:"Submitted prompt", max_tokens:200, knowledge_enabled:false}});
  await saving;
  const comparison = configurationDraftBaseline(host);
  assert.equal(host._configData, read, "the ACK cannot rewind the saved projection");
  assert.equal(comparison.config.prompt, "Submitted prompt");
  assert.equal(comparison.config.max_tokens, 200);
  assert.equal(comparison.config.knowledge_enabled, true, "the ACK owns only its submitted fields");
  assert.equal(comparison.title, "Edited title");
  const latest = {revision:"r3", title:"Edited title", config:{prompt:"Submitted prompt", max_tokens:200, knowledge_enabled:false}};
  const recovered = mergeConfigurationRead(host, {draft:host._draft, title:host._draftTitle}, read, latest);
  assert.equal(recovered.draft.prompt, "Saved prompt");
  assert.equal(recovered.title, "Saved title");
  assert.equal(recovered.draft.max_tokens, 200);
  assert.equal(recovered.draft.knowledge_enabled, false, "read-adopted values are not resurrected as authored edits");
});

test("a detached full acknowledgement requires a fresh full destination read before delta transfer", async () => {
  const source = panel(), gate = deferred();
  source.hass = {user:{id:"admin"}};
  source._call = () => gate.promise;
  const saving = saveConfiguration(source, {});
  source._draftTitle = "Saved title";
  source.isConnected = false;
  gate.resolve({valid:true, revision:"r2", title:"Edited title", config:{prompt:"Submitted prompt", max_tokens:200, knowledge_enabled:false}});
  await saving;
  const acknowledgement = source._eocAcknowledgedConfiguration;
  assert.equal(typeof acknowledgement, "function");
  const destination = panel(), read = deferred();
  destination.hass = source.hass;
  destination.isConnected = true;
  destination._data = {is_admin:true};
  let reads = 0, retries = 0;
  destination._loadConfigDraft = async options => {
    reads++;
    assert.deepEqual(options, {preserveResult:true, full:true});
    await read.promise;
    destination._configData = {revision:"r3", title:"Latest saved title", config:{prompt:"Latest prompt", max_tokens:300, knowledge_enabled:true}};
    destination._configDataStale = false;
    destination._eocConfigurationReadEpoch = 1;
    destination._result = destination._configData;
  };
  const retry = () => { retries++; };
  assert.deepEqual(acknowledgement(destination, retry), {accepted:false, refreshing:true});
  assert.deepEqual(acknowledgement(destination, retry), {accepted:false, refreshing:true});
  assert.equal(reads, 1);
  read.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(retries, 1);
  assert.deepEqual(acknowledgement(destination, retry), {accepted:true, refreshing:false});
  assert.equal(source._configData.title, "Edited title");
  assert.equal(source._draftTitle, "Saved title");
  assert.equal(destination._configData.revision, "r3");
  assert.equal(destination.renders, 1, "even a clean saved source must update mounted controls");
});

test("refreshing an acknowledged write never dispatches a new save or follows another owner", async () => {
  for (const replace of [false, true]) {
    const host = panel(), read = deferred();
    host._eocAcknowledgedConfiguration = () => {};
    host._configDataStale = true;
    let writes = 0;
    host._call = () => { writes++; throw new Error("No write until review"); };
    host._loadConfigDraft = async () => { await read.promise; host._configDataStale = false; };
    const refreshing = saveConfiguration(host, {});
    if (replace) host._agentId = "b";
    read.resolve(); await refreshing;
    assert.equal(writes, 0);
    assert.equal(host.toasts.length, replace ? 0 : 1);
    if (!replace) assert.match(host.toasts[0][0], /Review your changes before saving/);
  }
});

for (const writtenEffort of [false, true]) {
  test(`retiring model defaults respects ${writtenEffort ? "submitted" : "newer untouched"} reasoning fields`, async () => {
    const host = panel(), gate = deferred();
    host._configData.config.chat_model = "gpt-5.2";
    host._configData.config.reasoning_effort = writtenEffort ? "high" : "low";
    host._draft = {...host._configData.config, chat_model:"gpt-5", reasoning_effort:"low"};
    host._eocDirtyConfigKeys = new Set(["chat_model", ...(writtenEffort ? ["reasoning_effort"] : [])]);
    host._eocModelIntent = {acknowledgeReasoning(panel, _saved, _submission, comparison) { panel._draft.reasoning_effort = comparison.reasoning_effort; return true; }};
    host._call = () => gate.promise;
    const saving = saveConfiguration(host, {});
    host._draft.reasoning_effort = "minimal";
    const read = host._configData = {revision:"r3", title:"Saved title", config:{...host._configData.config, chat_model:"gpt-5", reasoning_effort:"high"}};
    host._eocConfigurationReadEpoch = 1;
    gate.resolve({valid:true, revision:"r2", title:"Saved title", config:{...read.config, reasoning_effort:"low"}});
    await saving;
    assert.equal(host._draft.reasoning_effort, writtenEffort ? "low" : "high");
    const latest = mergeConfigurationRead(host, {draft:host._draft, title:host._draftTitle}, read, read);
    assert.equal(latest.draft.reasoning_effort, "high", "computed values must not become local edits hiding newer saved state");
  });
}

test("discard drops the authored delta while retaining the acknowledged write's refresh obligation", async () => {
  const host = panel(), gate = deferred();
  host._setConfigDirty = value => {host._configDirty = value;};
  host._call = () => gate.promise;
  const saving = saveConfiguration(host, {});
  host._draftTitle = "Saved title";
  host._draft.prompt = "Saved prompt";
  const read = host._configData = {revision:"r1", title:"Saved title", config:{...host._configData.config, knowledge_enabled:true}};
  host._eocConfigurationReadEpoch = 1;
  host._loadConfigDraft = async () => { throw new Error("Saved configuration unavailable"); };
  gate.resolve({valid:true, revision:"r2", title:"Edited title", config:{prompt:"Submitted prompt", max_tokens:200, knowledge_enabled:false}});
  await saving;
  const intent = host._eocAcknowledgedConfiguration;
  assert.equal(typeof intent, "function");
  assert.equal(discardConfigurationDraft(host), true);
  assert.equal(host._draftTitle, "Edited title");
  assert.equal(host._draft.prompt, "Submitted prompt");
  assert.equal(host._draft.max_tokens, 200);
  assert.equal(host._draft.knowledge_enabled, true, "do not rewind unrelated fields from the accepted read");
  assert.equal(host._configData, read, "discard is not a new certification of the saved revision");
  assert.equal(host._eocAcknowledgedConfiguration, intent);
  assert.equal(host._configDataStale, true);
  assert.equal(host._configDirty, false);
  const latest = {revision:"r3", title:"Newest title", config:{prompt:"Newest prompt", max_tokens:300, knowledge_enabled:false}};
  const merged = mergeConfigurationRead(host, {draft:host._draft, title:host._draftTitle}, read, latest);
  assert.equal(merged.title, "Newest title");
  assert.deepEqual(merged.draft, latest.config, "discarded values must not survive the next read as local edits");
});

test("discard cannot clear a configuration draft while its write is pending", () => {
  const host = panel(), draft = host._draft;
  host._configurationSaving = true;
  assert.equal(discardConfigurationDraft(host), false);
  assert.equal(host._draft, draft);
  assert.equal(host._draft.prompt, "Submitted prompt");
});
