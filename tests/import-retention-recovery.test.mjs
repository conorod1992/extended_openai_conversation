import test from "node:test";
import assert from "node:assert/strict";
import {fixture, edit, retire, replace, saveConfiguration, flushUntil, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery} from "./management-ownership-harness.mjs";
import {applyConfigurationImport, refreshUncertainConfigurationImport} from "../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js";
import {pendingRetentionFields} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";
import {newVoiceMappingRow, updateVoiceMappingRows, voiceMappingDraftError, voiceMappingDraftDirty} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";
import {captureConfigurationSubmission, retireReplacedConfigurationDraft, configurationAuthoringBaseline, configurationDraftBaseline} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
import {TestEventTarget} from "./frontend-test-helpers.mjs";
import {startStoredConfigurationPrefetch, ENTRY_KEY} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";

function scenario({failRead = false, mode = "current", unchangedImport = false} = {}) {
  const f = fixture();
  if (unchangedImport) for (const record of f.state.records.values()) record.config.usage_run_retention_days = 30;
  const panel = f.create(), send = panel.hass.callWS;
  const input = {value:"reviewed import"}, button = {disabled:false}, summary = {textContent:""};
  const dialog = {open:true, querySelectorAll:() => [], close() {this.open=false;}};
  const controls = new Map([["#import-document", input], ["#import-apply", button], ["#import-summary", summary],
    ['input[name="import-mode"]:checked', {value:mode}], ["#import-dialog", dialog]]);
  panel.shadowRoot.querySelector = key => controls.get(key) || null;
  panel.shadowRoot.querySelectorAll = key => key === "dialog[open]" && dialog.open ? [dialog] : [];
  Object.assign(panel, {_importDocument:input.value, _importMode:mode, _importRevision:panel._configData.revision});
  panel._loadSection = () => panel._loadConfigDraft({preserveResult:true});
  let failed = false, readFailed = false;
  panel.hass.callWS = async request => {
    if (request.action === "agents" && !request.section) return {is_admin:true, agents:structuredClone(f.state.agents), scopes:[]};
    if (request.section === "configuration" && request.action === "import") {
      if (mode === "current" && !unchangedImport) {
        const record = f.state.records.get(panel._agentId);
        Object.assign(record, {title:"Imported", revision:failed ? "r2" : "r1",
          config:{...record.config, max_tokens:1600, usage_request_retention_days:0, usage_run_retention_days:30, voice_device_mappings:{imported:"shared"}}});
      }
      if (!failed) {failed=true; throw {code:"eoai_mutation_committed", message:"retention deletion failed"};}
      return {status:mode === "current" ? "updated" : "created", subentry_id:panel._agentId};
    }
    if (request.section === "configuration" && request.action === "get" && failed && failRead && !readFailed) {
      readFailed = true; throw new Error("Refresh unavailable");
    }
    return send(request);
  };
  return {...f, panel, input, button};
}

for (const failRead of [false, true]) test(`committed import retains deletion across replacement; initial read failed=${failRead}`, async () => {
  const f = scenario({failRead}), source = f.panel;
  edit(source, "max_tokens", 777);
  await applyConfigurationImport(source, source.shadowRoot);
  assert.equal(source._configDirty, true);
  const target = replace(source, f.create);
  try {
    await flushUntil(() => !source._configDirty);
    assert.equal(target._draft.max_tokens, 1600, "the confirmed overwrite retires the submitted draft");
    assert.deepEqual(new Set(pendingRetentionFields(target)), new Set(["usage_request_retention_days", "usage_run_retention_days"]));
    target._draftTitle = "Later title";
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {usage_request_retention_days:0, usage_run_retention_days:30});
    assert.equal(f.state.writes[0].revision, "r1");
    assert.equal(target._configDirty, false);
  } finally { retire(source, target); }
});

test("a failed committed-import refresh retries through an owned fresh configuration read", async () => {
  const {panel, input} = scenario({failRead:true});
  try {
    await applyConfigurationImport(panel, panel.shadowRoot);
    assert.equal(panel._configDataStale, true);
    assert.equal(await refreshUncertainConfigurationImport(panel, {configuration:true}), true);
    assert.equal(panel._configData.revision, "r1");
    assert.equal(panel._configDataStale, false);
    assert.equal(panel._eocImportRecovery, null);
    assert.equal(input.value, "reviewed import");
  } finally { retire(panel); }
});

test("later edits survive committed-import refresh and detached completion cannot revive its source", async () => {
  const f = scenario(), source = f.panel, read = f.state.holdRead();
  edit(source, "max_tokens", 777);
  const importing = applyConfigurationImport(source, source.shadowRoot);
  await flushUntil(() => read.entered);
  edit(source, "temperature", 0.8);
  const target = replace(source, f.create);
  try {
    await flushUntil(() => !source._configDirty);
    assert.equal(target._draft.max_tokens, 1600);
    assert.equal(target._draft.temperature, 0.8);
    read.release.resolve("success");
    await importing;
    assert.equal(source._configDirty, false);
    assert.equal(source._eocPendingRetention, null);
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {temperature:0.8, usage_request_retention_days:0, usage_run_retention_days:30});
  } finally { retire(source, target); }
});

for (const newerRows of [false, true]) test(`committed import retires only submitted Voice rows; newer rows=${newerRows}`, async () => {
  const f = scenario(), panel = f.panel;
  updateVoiceMappingRows(panel, [newVoiceMappingRow()]);
  panel._syncConfigDirty();
  const read = f.state.holdRead();
  const importing = applyConfigurationImport(panel, panel.shadowRoot);
  await flushUntil(() => read.entered);
  if (newerRows) updateVoiceMappingRows(panel, [{id:"new-row", deviceId:"later-device", owner:"shared", satellite:"", lookup:""}]);
  read.release.resolve("success");
  await importing;
  try {
    assert.equal(voiceMappingDraftError(panel), "");
    assert.equal(voiceMappingDraftDirty(panel), newerRows);
    await saveConfiguration(panel, null);
    assert.equal(f.state.writes.length, 1, "retired invalid rows cannot block the retention retry");
    if (newerRows) assert.deepEqual(f.state.writes[0].config.voice_device_mappings, {"later-device":"shared"});
    else assert.equal(Object.hasOwn(f.state.writes[0].config, "voice_device_mappings"), false);
  } finally { retire(panel); }
});

for (const boundary of ["authentication", "draft", "selection"]) test(`import refresh cannot acquire a newer ${boundary} owner`, async () => {
  const f = scenario(), panel = f.panel, baseline = panel._configData, read = f.state.holdRead();
  const importing = applyConfigurationImport(panel, panel.shadowRoot);
  await flushUntil(() => read.entered);
  if (boundary === "authentication") panel._eocAuthentication = {userId:panel.hass.user.id, generation:1};
  if (boundary === "draft") panel._eocDraftGeneration = 1;
  if (boundary === "selection") panel._agentId = f.agentB;
  read.release.resolve("success");
  await importing;
  try {
    assert.equal(panel._configData, baseline);
    assert.deepEqual(pendingRetentionFields(panel), []);
    assert.deepEqual(panel.toasts, []);
  } finally { retire(panel); }
});

test("a committed new-assistant import does not retire or retry the selected source's retention", async () => {
  const {panel} = scenario({mode:"new"});
  const draft = structuredClone(panel._draft);
  try {
    await applyConfigurationImport(panel, panel.shadowRoot);
    assert.deepEqual(panel._draft, draft);
    assert.deepEqual(pendingRetentionFields(panel), []);
    assert.equal(panel._eocRestoredConfiguration, undefined);
    assert.equal(panel._configDirty, false);
  } finally { retire(panel); }
});

test("a replacement preserves newer invalid Voice rows even when their persistence map did not change", () => {
  const {create} = fixture(), panel = create();
  const submitted = captureConfigurationSubmission(panel);
  updateVoiceMappingRows(panel, [newVoiceMappingRow()]);
  try {
    assert.deepEqual(panel._draft, submitted.draft);
    retireReplacedConfigurationDraft(panel, submitted);
    assert.ok(panel._draft);
    assert.match(voiceMappingDraftError(panel), /Choose an Assist satellite/);
    assert.equal(voiceMappingDraftDirty(panel), true);
  } finally { retire(panel); }
});

function captureRecoveryDialogs(panel) {
  TestEventTarget.prototype.append = function() {};
  globalThis.document = {createElement(tagName) {
    return {tagName, dataset:{}, children:[], handlers:{}, append(...nodes) {this.children.push(...nodes);},
      setAttribute() {}, addEventListener(type, callback) {this.handlers[type]=callback;},
      showModal() {this.open=true;}, close() {this.open=false; this.handlers.close?.();}, remove() {}};
  }};
  panel.shadowRoot.append = dialog => {panel.recoveryDialog=dialog;};
}

for (const variant of ["clean", "retired", "newer"]) test(`recovery compares only post-overwrite source edits: ${variant}`, async () => {
  const f = scenario({failRead:true}), source = f.panel;
  if (variant !== "clean") {
    edit(source, "max_tokens", 777);
    edit(source, "usage_request_retention_days", 7);
    source._draftTitle = "Retired title";
  }
  await applyConfigurationImport(source, source.shadowRoot);
  const saved = source._configData;
  if (variant === "newer") edit(source, "max_tokens", 999);
  const target = replace(source, (...args) => {
    const p = f.create(...args);
    edit(p, "max_tokens", 888);
    p._draftTitle = "Later destination title";
    captureRecoveryDialogs(p);
    return p;
  });
  try {
    await flushUntil(() => variant === "newer" ? target.recoveryDialog?.open : !source._configDirty);
    assert.equal(source._configData, saved, "authoring comparison does not rewrite the saved baseline");
    assert.equal(source._configData.revision, "r0");
    if (variant === "newer") {
      assert.equal(target.recoveryDialog.open, true);
      const copy = JSON.parse(target.recoveryDialog.children.find(node => node.tagName === "textarea").value);
      assert.deepEqual(copy, {config:{max_tokens:999}}, "the copy excludes overwritten fields and title");
      assert.equal(source._configDirty, true);
      target.recoveryDialog.children.find(node => node.textContent === "Discard recovered edits").onclick();
      await flushUntil(() => pendingRetentionFields(target).length === 2);
    } else assert.equal(target.recoveryDialog?.open ?? false, false);
    assert.equal(target._draft.max_tokens, 888);
    assert.equal(target._draftTitle, "Later destination title");
    assert.equal(pendingRetentionFields(target).length, 2);
    assert.equal(source._configDirty, false);
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {max_tokens:888, usage_request_retention_days:0, usage_run_retention_days:30});
    assert.equal(f.state.writes[0].revision, "r1");
  } finally { retire(source, target); }
});

test("shared restore retirement ignores overwritten settings while recovering a later title", async () => {
  const f = fixture(), source = f.create();
  edit(source, "usage_request_retention_days", 7);
  const submitted = captureConfigurationSubmission(source);
  // A confirmed configuration-only restore changes the persisted snapshot;
  // the later title belongs to the next save and keeps this source retained.
  Object.assign(f.state.records.get(source._agentId), {revision:"r1", title:"Restored",
    config:{...source._configData.config, usage_request_retention_days:0}});
  source._draftTitle = "Authored after restore";
  retireReplacedConfigurationDraft(source, submitted);
  source._syncConfigDirty();
  const target = replace(source, (...args) => {
    const p = f.create(...args);
    edit(p, "usage_request_retention_days", 30);
    captureRecoveryDialogs(p);
    return p;
  });
  try {
    await flushUntil(() => !source._configDirty);
    assert.equal(target.recoveryDialog?.open ?? false, false);
    assert.equal(target._draft.usage_request_retention_days, 30);
    assert.equal(target._draftTitle, "Authored after restore");
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {usage_request_retention_days:30});
    assert.equal(f.state.writes[0].title, "Authored after restore");
    assert.equal(f.state.writes[0].revision, "r1");
  } finally { retire(source, target); }
});

for (const boundary of ["authentication", "draft", "selection"]) test(`retirement cannot suppress authoring under a newer ${boundary} owner`, async () => {
  const f = scenario({failRead:true}), panel = f.panel;
  edit(panel, "max_tokens", 777);
  await applyConfigurationImport(panel, panel.shadowRoot);
  try {
    assert.equal(configurationAuthoringBaseline(panel).config.max_tokens, 777);
    if (boundary === "authentication") panel._eocAuthentication = {userId:panel.hass.user.id, generation:1};
    if (boundary === "draft") panel._eocDraftGeneration = 1;
    if (boundary === "selection") panel._agentId = f.agentB;
    assert.equal(configurationAuthoringBaseline(panel), configurationDraftBaseline(panel));
  } finally { retire(panel); }
});

for (const variant of ["equal incoming value", "failed read", "newer destination edit"]) test(`an unread destination overwrite resolves before recovery: ${variant}`, async () => {
  const f = scenario({failRead:true}), target = f.panel, source = f.create();
  edit(source, "max_tokens", 777);
  edit(target, "max_tokens", 777);
  await applyConfigurationImport(target, target.shadowRoot);
  target.shadowRoot.querySelector("#import-dialog").close();
  captureRecoveryDialogs(target);
  if (variant === "newer destination edit") edit(target, "max_tokens", 888);
  const send = target.hass.callWS;
  let fail = variant === "failed read";
  target.hass.callWS = async request => {
    if (request.action === "get" && fail) throw new Error("Destination read unavailable");
    return send(request);
  };
  source.isConnected = false;
  retainPanelDrafts(source);
  stopDraftRecovery(source);
  restorePanelDrafts(target);
  try {
    if (fail) {
      await flushUntil(() => target.toasts.some(message => message.includes("complete settings")));
      assert.equal(source._configDirty, true);
      assert.equal(target._configData.revision, "r0");
      assert.ok(target._eocRestoredConfiguration);
      fail = false;
      target._loadToken++;
      restorePanelDrafts(target);
    }
    await flushUntil(() => target._configData.revision === "r1" && !target._configDataStale);
    assert.equal(target._eocRestoredConfiguration, null);
    if (variant === "newer destination edit") {
      await flushUntil(() => target.recoveryDialog?.open);
      assert.equal(target._draft.max_tokens, 888);
      assert.equal(source._configDirty, true);
    } else {
      await flushUntil(() => !source._configDirty);
      assert.equal(target._draft.max_tokens, 777, "an incoming edit equal to the retired value remains authored");
      await saveConfiguration(target, null);
      assert.equal(f.state.writes[0].config.max_tokens, 777);
      assert.equal(f.state.writes[0].revision, "r1");
    }
  } finally { retire(source, target); }
});

for (const failRead of [false, true]) test(`committed import refreshes an older full destination before recovery; source read failed=${failRead}`, async () => {
  const f = scenario({failRead}), source = f.panel, target = f.create();
  edit(source, "usage_request_retention_days", 7);
  edit(target, "max_tokens", 888);
  await applyConfigurationImport(source, source.shadowRoot);
  source.shadowRoot.querySelector("#import-dialog").close();
  source.isConnected = false;
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  try {
    await flushUntil(() => !source._configDirty);
    assert.equal(target._configData.revision, "r1");
    assert.equal(target._configDataStale, false);
    assert.equal(target._draftTitle, "Imported");
    assert.equal(target._draft.max_tokens, 888);
    assert.equal(target._draft.usage_request_retention_days, 0);
    assert.equal(pendingRetentionFields(target).length, 2);
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {max_tokens:888, usage_request_retention_days:0, usage_run_retention_days:30});
    assert.equal(f.state.writes[0].revision, "r1");
    assert.equal(target._configDirty, false);
  } finally { retire(source, target); }
});

for (const retry of ["new visit", "owned full read"]) test(`an older full destination retries a failed committed-recovery read: ${retry}`, async () => {
  const f = scenario({failRead:true}), source = f.panel, target = f.create(), send = target.hass.callWS;
  edit(target, "max_tokens", 888);
  await applyConfigurationImport(source, source.shadowRoot);
  let fail = true, reads = 0;
  target.hass.callWS = async request => {
    if (request.section === "configuration" && request.action === "get") {
      reads++;
      if (fail) throw new Error("Destination refresh unavailable");
    }
    return send(request);
  };
  source.isConnected = false; retainPanelDrafts(source); restorePanelDrafts(target);
  try {
    await flushUntil(() => target.toasts.some(message => message.includes("complete settings")));
    assert.equal(source._configDirty, true);
    assert.equal(pendingRetentionFields(source).length, 2);
    assert.equal(pendingRetentionFields(target).length, 0);
    assert.equal(target._configDataStale, true);
    for (let i = 0; i < 5; i++) restorePanelDrafts(target);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(reads, 1, "failed recovery does not loop on render");
    fail = false;
    if (retry === "new visit") target._loadToken++;
    else await target._loadConfigDraft({preserveResult:true, full:true});
    restorePanelDrafts(target);
    await flushUntil(() => !source._configDirty);
    assert.equal(reads, 2);
    assert.equal(target._draft.max_tokens, 888);
    assert.equal(target._draft.usage_request_retention_days, 0);
    assert.equal(target._configData.revision, "r1");
  } finally { retire(source, target); }
});

test("an unchanged committed import can certify the same revision without looping", async () => {
  const f = scenario({failRead:true, unchangedImport:true}), source = f.panel, target = f.create();
  edit(source, "usage_request_retention_days", 7);
  edit(target, "max_tokens", 888);
  await applyConfigurationImport(source, source.shadowRoot);
  source.isConnected = false; retainPanelDrafts(source); restorePanelDrafts(target);
  try {
    await flushUntil(() => !source._configDirty);
    assert.equal(target._configData.revision, "r0");
    assert.equal(target._draft.usage_request_retention_days, 30);
    assert.equal(pendingRetentionFields(target).length, 2);
    await saveConfiguration(target, null);
    assert.equal(f.state.writes[0].revision, "r0");
    assert.deepEqual(f.state.writes[0].config, {max_tokens:888, usage_request_retention_days:30, usage_run_retention_days:30});
  } finally { retire(source, target); }
});

test("multiple unread recoveries retain independent read receipts while Voice rows remain deferred", async () => {
  const f = scenario({failRead:true}), source = f.panel, second = f.create(), target = f.create();
  updateVoiceMappingRows(target, [newVoiceMappingRow()]); target._syncConfigDirty();
  edit(target, "max_tokens", 888);
  await applyConfigurationImport(source, source.shadowRoot);
  updateVoiceMappingRows(source, [newVoiceMappingRow()]); source._syncConfigDirty();
  const submitted = captureConfigurationSubmission(second);
  f.state.records.get(source._agentId).revision = "r2";
  retireReplacedConfigurationDraft(second, submitted, {retainUntilRead:true});
  updateVoiceMappingRows(second, [newVoiceMappingRow()]); second._syncConfigDirty();
  const send = target.hass.callWS;
  let reads = 0;
  target.hass.callWS = request => {
    if (request.section === "configuration" && request.action === "get") reads++;
    return send(request);
  };
  for (const panel of [source, second]) { panel.isConnected = false; retainPanelDrafts(panel); }
  restorePanelDrafts(target);
  try {
    await flushUntil(() => !source._configDirty && !second._configDirty);
    assert.equal(reads, 2);
    assert.equal(target._configData.revision, "r2");
    assert.equal(target._draft.max_tokens, 888);
    assert.equal(voiceMappingDraftDirty(source), true);
    assert.equal(voiceMappingDraftDirty(second), true);
    for (let i = 0; i < 5; i++) restorePanelDrafts(target);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(reads, 2, "deferred Voice scopes do not alternate fresh reads indefinitely");
    assert.equal(pendingRetentionFields(target).length, 2);
  } finally { retire(source, second, target); }
});

for (const failure of ["none", "source read", "destination read", "destination read, same visit", "unchanged import"]) test(`committed recovery cannot consume a pre-import configuration prefetch: ${failure}`, async () => {
  const f = scenario({failRead:failure !== "none", unchangedImport:failure === "unchanged import"});
  const source = f.panel, target = f.create(), storage = globalThis.localStorage;
  target._clearConfigDraft();
  globalThis.localStorage = {getItem:key => key === ENTRY_KEY ? f.state.entry : null, setItem() {}};
  const prefetch = startStoredConfigurationPrefetch(target, target._agentId);
  await prefetch.promise;
  edit(source, "usage_request_retention_days", 7);
  await applyConfigurationImport(source, source.shadowRoot);
  const send = target.hass.callWS;
  let fail = failure.startsWith("destination read"), reads = 0;
  target.hass.callWS = request => {
    if (request.section === "configuration" && request.action === "get") {
      reads++;
      if (fail) return Promise.reject(new Error("Destination refresh unavailable"));
    }
    return send(request);
  };
  source.isConnected = false; retainPanelDrafts(source); restorePanelDrafts(target);
  try {
    if (fail) {
      await flushUntil(() => target.toasts.some(message => message.includes("complete settings")));
      assert.equal(source._configDirty, true);
      assert.equal(pendingRetentionFields(target).length, 0);
      assert.equal(target._configData, null);
      fail = false;
      if (failure.endsWith("same visit")) await target._loadConfigDraft({preserveResult:true, full:true});
      else target._loadToken++;
      restorePanelDrafts(target);
    }
    await flushUntil(() => !source._configDirty);
    assert.equal(prefetch.detail.status, "discarded");
    assert.equal(prefetch.detail.reason, "committed-recovery");
    assert.equal(reads, failure.startsWith("destination read") ? 2 : 1);
    const unchanged = failure === "unchanged import";
    assert.equal(target._configData.revision, unchanged ? "r0" : "r1");
    assert.equal(target._draft.usage_request_retention_days, unchanged ? 30 : 0);
    assert.equal(target._configDataStale, false);
    await saveConfiguration(target, null);
    assert.equal(f.state.writes[0].revision, unchanged ? "r0" : "r1");
    assert.deepEqual(f.state.writes[0].config, {usage_request_retention_days:unchanged ? 30 : 0, usage_run_retention_days:30});
    assert.equal(target._configDirty, false);
  } finally { globalThis.localStorage = storage; retire(source, target); }
});
