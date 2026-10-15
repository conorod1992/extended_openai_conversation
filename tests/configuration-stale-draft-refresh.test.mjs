import assert from "node:assert/strict";
import test from "node:test";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

globalThis.window = {location:{pathname:"/extended-openai/assistant/prompt-context"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};

const {ExtendedOpenAIManagementPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
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
