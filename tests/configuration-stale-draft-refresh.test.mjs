import assert from "node:assert/strict";
import test from "node:test";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

globalThis.window = {location:{pathname:"/extended-openai/assistant/prompt-context"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};

const {ExtendedOpenAIManagementPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
const {renderExposedAttributeSettings, bindExposedAttributeSettings} = await import("../custom_components/extended_openai_conversation_responses/frontend/exposed-attributes-ui.js");
const {bindTools, openTool} = await import("../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js");
const {bindConfiguration} = await import("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js");
const {updateConfigurationControl} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-inputs.js");
const {confirmStateSafeNavigation} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js");
const {saveConfiguration} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-actions.js");
const {captureConfigurationSubmission, reconcileConfigurationAcknowledgement} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js");

class Control extends TestEventTarget {
  constructor(id) {
    super();
    Object.assign(this, {id, value:"", dataset:{}, parentElement:{inert:false}, disabled:false, attributes:new Map()});
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  removeAttribute(key) { this.attributes.delete(key); }
  showModal() { this.open = true; }
  close() { this.open = false; void this.emit("close"); }
  focus() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

const tool = (name) => ({spec:{name, description:"Test tool", parameters:{type:"object", properties:{}}}, function:{type:"template", value_template:"ok"}});

test("warm Prompt navigation loads absent metadata through the real request lifecycle", async () => {
  const {host} = panel();
  delete host._configData.exposed_attribute_catalog;
  host._hass = {callWS: async message => {
    assert.equal(message.action, "live_metadata");
    return {exposed_attribute_catalog:{entities:[{entity_id:"light.current"}]}};
  }};
  await host._loadSectionData();
  await Promise.all([...host._eocLiveMetadataPending.values()].map(record => record.promise));
  assert.deepEqual(host._result.exposed_attribute_catalog.entities, [{entity_id:"light.current"}]);
});

function panel() {
  const host = new ExtendedOpenAIManagementPanel();
  const ids = ["tool-dialog", "tool-dialog-title", "tool-dialog-meta", "tool-yaml", "tool-error", "built-in-picker", "built-in-function", "tool-save", "tool-validate", "tool-cancel",
    "group-dialog", "group-name", "group-id", "group-description", "group-loading-mode", "group-save", "group-error",
    "preview-request", "prompt-preview-dialog", "prompt-preview-status", "request-preview-sections", "prompt-preview-notes", "copy-prompt-preview", "request-footprint", "function-group-savings"];
  const controls = Object.fromEntries(ids.map(id => [id, new Control(id)]));
  const shadow = new TestEventTarget();
  shadow.querySelector = selector => controls[selector.replace(/^#/, "")] || null;
  shadow.querySelectorAll = selector => selector === "#group-functions input:checked" ? [{value:"old_tool"}] : [];
  host.shadowRoot = shadow;
  Object.assign(host, {
    _agentId:"a", _draftAgentId:"a", _page:"assistant", _subsection:"prompt-context",
    _data:{agents:[{entry_id:"e", subentry_id:"a"}], is_admin:true},
    _render() {}, _toast() {}, _setSaving(button, saving) { if (button) button.disabled = saving; },
    _confirm() { throw new Error("Same-assistant navigation must retain the shared draft without a discard prompt"); },
  });
  const config = {prompt:"Saved prompt", temperature:1, top_p:1,
    functions:[tool("old_tool")], function_groups:[{id:"old_group", name:"Group", description:"Group tools", loading_mode:"on_demand", functions:["old_tool"]}],
    guest_policy_version:2, guest_function_policy:"custom", guest_allowed_function_names:["old_tool"], guest_allowed_group_ids:["old_group"], chat_model:"gpt-4.1"};
  host._configData = {revision:"r1", title:"Saved title", config:structuredClone(config), exposed_attribute_catalog:{}};
  host._draft = structuredClone(config);
  host._draftTitle = host._configData.title;
  host._result = host._configData;
  const edit = (key, value) => {
    const control = new Control(key);
    control.dataset.config = key;
    control.value = value;
    if (typeof value === "number") control.dataset.type = "number";
    updateConfigurationControl(host, control);
  };
  return {host, controls, edit};
}

for (const kind of ["fresh", "cached", "retention", "partial", "malformed", "unrelated"]) {
  test(`configuration read repair health is conservative for ${kind} metadata`, () => {
    const {host} = panel();
    const issue = {field:kind === "unrelated" ? "provider" : "functions", repairable:true, message:"Existing issue"};
    host._selectedAgent().configuration_issue = issue;
    const snapshot = structuredClone(host._configData);
    if (kind === "retention") snapshot.projection = "retention";
    if (kind === "partial") delete snapshot.config.function_groups;
    if (kind === "malformed") snapshot.function_repair = {};
    host._observeConfigurationRead(snapshot, {fresh:kind !== "cached"});
    assert.equal(host._eocConfigurationReadEpoch, 1);
    if (kind === "fresh") assert.equal(host._selectedAgent().configuration_issue, undefined);
    else assert.equal(host._selectedAgent().configuration_issue, issue);
  });
}

test("a fresh complete read updates an active Function repair issue", () => {
  const {host} = panel();
  const snapshot = {...structuredClone(host._configData), function_repair:{
    invalid_tools:[], invalid_count:0, group_issues:[{id:"damaged"}],
    tools_valid:true, validation_error:"Group references an unavailable Function",
  }};
  host._observeConfigurationRead(snapshot, {fresh:true});
  assert.deepEqual(host._selectedAgent().configuration_issue,
    {field:"functions", repairable:true, message:"Group references an unavailable Function"});
});

test("an accepted older read does not clear newer agent issue metadata", async () => {
  const {host} = panel(), response = deferred();
  host._configDataStale = true;
  host._selectedAgent().configuration_issue = {field:"functions", repairable:true, message:"Original issue"};
  host._hass = {callWS:async message => {
    assert.equal(message.section, "configuration");
    assert.equal(message.action, "get");
    return response.promise;
  }};
  const reading = host._loadConfigDraft();
  await Promise.resolve();
  const newer = {field:"functions", repairable:true, message:"Newer catalogue issue"};
  host._selectedAgent().configuration_issue = newer;
  response.resolve({...structuredClone(host._configData), revision:"r2"});
  await reading;
  assert.equal(host._selectedAgent().configuration_issue, newer);
  assert.equal(host._eocConfigurationReadEpoch, 1);
});

for (const kind of ["function", "group"]) {
  test(`dirty prompt and title survive a ${kind} rename, fresh preview and later save`, async () => {
    const {host, controls, edit} = panel();
    edit("prompt", "Unsaved prompt");
    edit("__title", "Unsaved title");
    assert.equal(await confirmStateSafeNavigation(host, "capabilities/functions"), true);
    host._page = "capabilities";
    host._subsection = "functions";
    const persisted = structuredClone(host._configData);
    persisted.revision = "r2";
    persisted.config.top_p = 0.75;
    if (kind === "function") {
      persisted.config.functions[0].spec.name = "new_tool";
      persisted.config.function_groups[0].functions = ["new_tool"];
      persisted.config.guest_allowed_function_names = ["new_tool"];
    } else {
      persisted.config.function_groups[0].id = "new_group";
      persisted.config.guest_allowed_group_ids = ["new_group"];
    }
    const read = deferred(), calls = [];
    host._hass = {callWS:async message => {
      calls.push(structuredClone(message));
      if (message.action === "serialize") return {yaml:JSON.stringify(host._draft.functions[0])};
      if (message.section === "tools") return {functions:structuredClone(persisted.config.functions), function_groups:structuredClone(persisted.config.function_groups), revision:"r2"};
      if (message.action === "get") return read.promise;
      if (message.action === "request_preview") return {sections:[], notes:[]};
      if (message.action === "save") return {valid:true, ...structuredClone(persisted), revision:"r3", title:message.title, config:{...structuredClone(persisted.config), ...message.config}};
      throw new Error(`Unexpected ${message.section}/${message.action}`);
    }};
    bindTools(host);
    if (kind === "function") {
      await openTool(host, 0);
      controls["tool-yaml"].value = JSON.stringify(persisted.config.functions[0]);
      await controls["tool-save"].emit("click");
      assert.equal(controls["tool-dialog"].open, false);
    } else {
      // The group editor's existing values and original ID feed its real Save listener.
      host._groupOriginalId = "old_group";
      controls["group-dialog"].showModal();
      for (const [id, value] of Object.entries({"group-name":"Group", "group-id":"new_group", "group-description":"Group tools", "group-loading-mode":"on_demand"})) controls[id].value = value;
      await controls["group-save"].emit("click");
      assert.equal(controls["group-dialog"].open, false);
    }
    assert.equal(host._configDataStale, true);
    assert.equal(await confirmStateSafeNavigation(host, "assistant/prompt-context"), true);
    host._page = "assistant";
    host._subsection = "prompt-context";
    const loading = host._loadSectionData();
    await Promise.resolve();
    assert.equal(calls.filter(call => call.action === "get").length, 1, "dirty drafts must refresh a stale saved baseline");
    edit("prompt", "Newer prompt while read is pending");
    edit("__title", "Newer title while read is pending");
    edit("temperature", 0.5);
    read.resolve(structuredClone(persisted));
    await loading;
    assert.equal(host._draft.prompt, "Newer prompt while read is pending");
    assert.equal(host._draftTitle, "Newer title while read is pending");
    assert.equal(host._draft.temperature, 0.5);
    assert.equal(host._draft.top_p, 0.75);
    assert.deepEqual(host._draft.guest_allowed_function_names, persisted.config.guest_allowed_function_names);
    assert.deepEqual(host._draft.guest_allowed_group_ids, persisted.config.guest_allowed_group_ids);
    assert.deepEqual([...host._eocDirtyConfigKeys].sort(), ["__title", "prompt", "temperature"]);
    assert.equal(host._configDataStale, false);
    assert.equal(host._freshCleanConfiguration("a", "full").revision, "r2");
    bindConfiguration(host);
    await controls["preview-request"].emit("click");
    const preview = calls.find(call => call.action === "request_preview").config;
    assert.deepEqual(preview.guest_allowed_function_names, persisted.config.guest_allowed_function_names);
    assert.deepEqual(preview.guest_allowed_group_ids, persisted.config.guest_allowed_group_ids);
    assert.equal(preview.prompt, "Newer prompt while read is pending");
    await saveConfiguration(host, new Control("save-config"));
    const save = calls.find(call => call.section === "configuration" && call.action === "save");
    assert.equal(save.revision, "r2");
    assert.deepEqual(save.config, {prompt:"Newer prompt while read is pending", temperature:0.5});
    assert.equal(save.title, "Newer title while read is pending");
    assert.equal(host._configDirty, false);
  });
}

test("revisiting prompt context refreshes HA-owned Assist exposure metadata", async () => {
  const {host} = panel();
  host._viewKey = () => "assistant/prompt-context";
  host._loadToken = 7;
  host._configData.exposed_attribute_catalog = {entities:[{entity_id:"light.old"}]};
  host._result = host._configData;
  host._eocLiveMetadataCache = new Map([["exposed_attribute_catalog", {
    agentId:"a", epoch:0, revision:"r1", value:{entities:[{entity_id:"light.old"}]},
  }]]);
  host._eocDirtyConfigKeys = new Set();
  let reads = 0;
  host._call = async (section, action, data) => {
    reads++;
    assert.equal(section, "configuration");
    assert.equal(action, "live_metadata");
    assert.deepEqual(data.metadata_keys, ["exposed_attribute_catalog"]);
    return {exposed_attribute_catalog:{entities:[{entity_id:"light.new"}]}};
  };
  await host._loadConfigurationLiveMetadata("assistant/prompt-context");
  assert.equal(Object.hasOwn(host._draft, "exposed_attribute_catalog"), false,
    "live Assist metadata must stay outside the persistable configuration draft");
  assert.equal(host._eocDirtyConfigKeys.has("exposed_attribute_catalog"), false,
    "refreshing read-only metadata must not add an invalid configuration save field");
  assert.deepEqual(host._configData.exposed_attribute_catalog.entities.map(item => item.entity_id), ["light.new"]);
  assert.deepEqual(host._result.exposed_attribute_catalog.entities.map(item => item.entity_id), ["light.new"]);
  await host._loadConfigurationLiveMetadata("assistant/prompt-context");
  assert.equal(reads, 1, "re-rendering the current visit must not fetch exposure twice");
  host._loadToken++;
  await host._loadConfigurationLiveMetadata("assistant/prompt-context");
  assert.equal(reads, 2, "a new visit still refreshes HA-owned exposure metadata");
});

for (const oldFirst of [false, true]) {
  test(`exposure reads from an abandoned visit cannot be adopted on return (${oldFirst ? "old" : "new"} response first)`, async () => {
    const {host} = panel();
    let view = "assistant/prompt-context";
    host._viewKey = () => view;
    host._loadToken = 1;
    host._result = host._configData;
    const baseline = host._configData.exposed_attribute_catalog;
    const reads = [];
    host._call = () => { const read = deferred(); reads.push(read); return read.promise; };
    const old = host._loadConfigurationLiveMetadata(view);
    view = "assistant/basics"; host._loadToken++;
    view = "assistant/prompt-context"; host._loadToken++;
    const fresh = host._loadConfigurationLiveMetadata(view);
    const coalesced = host._loadConfigurationLiveMetadata(view);
    assert.equal(reads.length, 2, "a new visit must dispatch a new request while coalescing reads within the visit");
    const result = entity => ({exposed_attribute_catalog:{entities:[{entity_id:entity}]}});
    if (oldFirst) {
      reads[0].resolve(result("light.obsolete")); await old;
      assert.equal(host._configData.exposed_attribute_catalog, baseline);
      assert.equal(host._eocLiveMetadataCache.has("exposed_attribute_catalog"), false);
      assert.equal(host._eocLiveMetadataPending.size, 1);
    }
    reads[1].resolve(result("light.current")); await Promise.all([fresh, coalesced]);
    if (!oldFirst) { reads[0].resolve(result("light.obsolete")); await old; }
    assert.equal(host._configData.exposed_attribute_catalog.entities[0].entity_id, "light.current");
    assert.equal(host._eocLiveMetadataCache.get("exposed_attribute_catalog").value.entities[0].entity_id, "light.current");
    assert.equal(host._eocLiveMetadataPending.size, 0);
  });
}

test("a background restore cannot discard another agent's pending Overview", async () => {
  const {host} = panel();
  host._data.agents.push({entry_id:"e", subentry_id:"b"});
  host._agentId = "b"; host._page = "overview"; host._subsection = "";
  host._viewKey = () => "overview";
  host._result = null;
  host._sectionCache.set("a|overview", {obsolete:true});
  host._eocLiveMetadataPending = new Map([["exposed_attribute_catalog", {agentId:"b", promise:Promise.resolve()}]]);
  const read = deferred(), started = deferred();
  host._call = () => { started.resolve(); return read.promise; };
  const loading = host._loadSectionData(); await started.promise;
  host._invalidateAfterMutation("a", "backup", "restore");
  assert.equal(host._sectionCache.has("a|overview"), false);
  assert.equal(host._eocLiveMetadataPending.get("exposed_attribute_catalog").agentId, "b");
  read.resolve({agent:{subentry_id:"b"}, title:"Agent B overview", usage:{}});
  await loading;
  assert.equal(host._result?.title, "Agent B overview");
  assert.equal(host._busy, false);
});

test("a mutation that supersedes the current Overview automatically retries its read", async () => {
  const {host} = panel(); host._viewKey = () => "overview";
  host._page = "overview"; host._subsection = "";
  host._result = null;
  const first = deferred(), second = deferred(), retried = deferred(); let reads = 0;
  host._call = () => {
    if (++reads === 1) return first.promise;
    retried.resolve(); return second.promise;
  };
  const loading = host._loadSectionData();
  host._invalidateAfterMutation("a", "backup", "restore");
  first.resolve({obsolete:true}); await loading; await retried.promise;
  second.resolve({title:"Fresh Overview", usage:{}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(host._result?.title, "Fresh Overview");
  assert.equal(host._busy, false);
  assert.equal(reads, 2);
});

test("a failed exposure refresh labels retained metadata and can retry without changing the draft", async () => {
  const {host} = panel();
  host._loadToken = 1;
  const catalog = {entities:[{entity_id:"light.old", name:"Old", reference:"old", durable_selection_available:true}]};
  host._configData.exposed_attribute_catalog = catalog;
  const draft = structuredClone(host._draft);
  let reads = 0;
  host._call = async () => {
    if (++reads === 1) throw new Error("Metadata unavailable");
    return {exposed_attribute_catalog:{entities:[]}};
  };
  await host._loadConfigurationLiveMetadata();
  assert.equal(host._configData.exposed_attribute_catalog, catalog);
  assert.match(renderExposedAttributeSettings(host), /Unable to refresh Assist exposure/);
  assert.match(renderExposedAttributeSettings(host), /last known/i);
  const retry = new Control("retry");
  host.shadowRoot.querySelector = selector => selector === "[data-retry-exposed-catalog]" ? retry : null;
  bindExposedAttributeSettings(host);
  await retry.emit("click");
  assert.equal(reads, 2);
  assert.deepEqual(host._configData.exposed_attribute_catalog.entities, []);
  assert.doesNotMatch(renderExposedAttributeSettings(host), /Unable to refresh Assist exposure/);
  assert.deepEqual(host._draft, draft);
});

test("an abandoned exposure failure cannot mark a newer visit as failed", async () => {
  const {host} = panel(); host._loadToken = 1;
  const old = deferred(); let reads = 0;
  host._call = () => ++reads === 1 ? old.promise : Promise.resolve({exposed_attribute_catalog:{entities:[]}});
  const loading = host._loadConfigurationLiveMetadata();
  host._loadToken++;
  await host._loadConfigurationLiveMetadata();
  old.reject(new Error("Obsolete failure")); await loading;
  assert.doesNotMatch(renderExposedAttributeSettings(host), /Unable to refresh Assist exposure/);
});

for (const change of ["agent", "entry", "route", "token", "generation", "revision", "full acknowledgement", "partial acknowledgement"]) {
  test(`stale refresh cannot overwrite a newer ${change}`, async () => {
    const {host, edit} = panel();
    edit("prompt", "Local prompt");
    host._configDataStale = true;
    const read = deferred();
    let reads = 0;
    host._call = () => { reads++; return read.promise; };
    const loading = host._loadConfigDraft();
    assert.equal(reads, 1);
    if (change === "agent") host._agentId = "b";
    if (change === "entry") host._data.agents[0].entry_id = "replacement";
    if (change === "route") host._subsection = "basics";
    if (change === "token") host._loadToken++;
    if (change === "generation") host._cacheGeneration++;
    if (change === "revision") host._applyToolRevision("tools", "set_enabled", {revision:"r3"}, {agentId:"a", entryId:"e"});
    if (change.endsWith("acknowledgement")) {
      const partial = change.startsWith("partial");
      reconcileConfigurationAcknowledgement(host,
        {revision:"r3", title:"Acknowledged title", config:partial ? {top_p:0.25} : {...host._draft, top_p:0.25}},
        captureConfigurationSubmission(host), partial ? {fields:["top_p"]} : {});
    }
    const baseline = host._configData, draft = structuredClone(host._draft);
    read.resolve({revision:"r2", title:"Obsolete", config:{prompt:"Obsolete"}});
    await loading;
    assert.equal(host._configData, baseline);
    assert.deepEqual(host._draft, draft);
    assert.equal(host._configDataStale, change !== "full acknowledgement");
    if (change === "full acknowledgement") assert.equal(host._freshCleanConfiguration("a", "full").revision, "r3");
  });
}

test("refresh merges presence and a revert to baseline using the latest draft", async () => {
  const {host, edit} = panel();
  edit("prompt", "Local prompt");
  host._configDataStale = true;
  const read = deferred();
  host._call = () => read.promise;
  const loading = host._loadConfigDraft();
  edit("prompt", "Saved prompt");
  delete host._draft.temperature;
  host._draft.local_added = false;
  const saved = structuredClone(host._configData);
  saved.revision = "r2";
  saved.config.prompt = "Remote prompt";
  saved.config.temperature = 0.5;
  delete saved.config.top_p;
  saved.config.remote_added = null;
  read.resolve(saved);
  await loading;
  assert.equal(host._draft.prompt, "Remote prompt", "a value reverted to baseline has no local delta");
  assert.equal(Object.hasOwn(host._draft, "temperature"), false, "keep a local deletion");
  assert.equal(Object.hasOwn(host._draft, "top_p"), false, "adopt a remote deletion");
  assert.equal(host._draft.local_added, false);
  assert.equal(host._draft.remote_added, null);
});

test("retention refresh and expansion preserve edits without dropping full fields", async () => {
  const {host, edit} = panel();
  host._page = "usage-maintenance";
  host._subsection = "retention";
  host._configData = {projection:"retention", revision:"r1", title:"Saved title", config:{usage_request_retention_days:30}};
  host._draft = structuredClone(host._configData.config);
  edit("usage_request_retention_days", 60);
  edit("__title", "Local title");
  host._configDataStale = true;
  const calls = [];
  host._call = async (_section, action) => {
    calls.push(action);
    return action === "retention_get"
      ? {projection:"retention", revision:"r2", title:"Remote title", config:{usage_request_retention_days:90, usage_run_retention_days:30}}
      : {revision:"r3", title:"Remote title", config:{usage_request_retention_days:90, usage_run_retention_days:30, prompt:"Full prompt"}};
  };
  await host._loadConfigDraft();
  assert.equal(host._configData.projection, "retention");
  assert.equal(host._draft.usage_request_retention_days, 60);
  host._page = "assistant";
  host._subsection = "prompt-context";
  await host._loadConfigDraft();
  assert.equal(host._draft.usage_request_retention_days, 60);
  assert.equal(host._draft.prompt, "Full prompt");
  assert.equal(host._draftTitle, "Local title");
  host._page = "usage-maintenance";
  host._subsection = "retention";
  host._invalidateCleanConfiguration("a");
  await host._loadConfigDraft();
  assert.equal(host._draft.prompt, "Full prompt");
  assert.deepEqual(calls, ["retention_get", "get", "get"]);
});

for (const separatePage of [false, true]) {
  test(`acknowledgement revalidation preserves the current page, separate=${separatePage}`, async () => {
    const {host, edit} = panel();
    edit("prompt", "Local prompt");
    host._configDataStale = true;
    const page = host._result = separatePage ? {sources:[{title:"Knowledge"}]} : host._configData;
    const saved = {...structuredClone(host._configData), revision:"r3"};
    saved.config.top_p = 0.25;
    host._call = async () => saved;
    await host._loadConfigDraft({preserveResult:true});
    assert.equal(host._configData, saved);
    assert.equal(host._eocConfigurationReadEpoch, 1);
    assert.equal(host._result, separatePage ? page : saved);
    assert.equal(host._draft.prompt, "Local prompt");
    assert.equal(host._draft.top_p, 0.25);
  });
}

for (const change of ["disconnect", "user"]) {
  test(`acknowledgement revalidation cannot publish after ${change} replacement`, async () => {
    const {host} = panel(), read = deferred();
    host._hass = {user:{id:"admin"}};
    host._configDataStale = true;
    host._call = () => read.promise;
    const baseline = host._configData, draft = host._draft;
    const loading = host._loadConfigDraft({preserveResult:true});
    if (change === "disconnect") host.isConnected = false;
    else host._hass = {user:{id:"other"}};
    read.resolve({revision:"r3", title:"Obsolete read", config:{prompt:"Obsolete"}});
    await loading;
    assert.equal(host._configData, baseline);
    assert.equal(host._draft, draft);
    assert.equal(host._configDataStale, true);
    assert.equal(host._eocConfigurationReadEpoch || 0, 0);
  });
}

for (const change of ["none", "title", "agent"]) {
  test(`fresh configuration title adoption respects intervening ${change} metadata`, async () => {
    const {host} = panel(), read = deferred();
    const selected = host._selectedAgent();
    selected.title = "Catalogue title";
    host._configDataStale = true;
    host._call = () => read.promise;
    const loading = host._loadConfigDraft();
    if (change === "title") selected.title = "Newer catalogue title";
    if (change === "agent") host._data.agents[0] = {...selected, title:"Newer catalogue title"};
    read.resolve({...structuredClone(host._configData), revision:"r3", title:"Fresh saved title"});
    await loading;
    assert.equal(host._selectedAgent().title, change === "none" ? "Fresh saved title" : "Newer catalogue title");
  });
}

for (const kind of ["cached", "retention", "incomplete"]) {
  test(`${kind} configuration cannot certify a catalogue title`, () => {
    const {host} = panel();
    host._selectedAgent().title = "Catalogue title";
    const saved = {...structuredClone(host._configData), title:"Uncertified title"};
    if (kind === "retention") saved.projection = "retention";
    if (kind === "incomplete") delete saved.config.function_groups;
    host._observeConfigurationRead(saved, {fresh:kind !== "cached", previousTitle:"Catalogue title"});
    assert.equal(host._selectedAgent().title, "Catalogue title");
  });
}

test("clearing a configuration draft retires its model work before the next owner loads", () => {
  const {host} = panel();
  host._eocModelChangeToken = 11;
  host._eocModelIntent = {};
  host._eocPendingModelPreparation = {draft:host._draft};
  host._eocReasoningChoiceRevision = 5;
  host._clearConfigDraft();
  assert.equal(host._eocModelChangeToken, 12);
  assert.equal(host._eocModelIntent, null);
  assert.equal(host._eocPendingModelPreparation, null);
  assert.equal(host._eocReasoningChoiceRevision, 0);
  assert.equal(host._draft, null);
  assert.equal(host._draftAgentId, null);
  assert.equal(host._configData, null);
});
