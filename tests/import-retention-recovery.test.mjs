import test from "node:test";
import assert from "node:assert/strict";
import {fixture, edit, retire, replace, saveConfiguration, flushUntil} from "./management-ownership-harness.mjs";
import {applyConfigurationImport, refreshUncertainConfigurationImport} from "../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js";
import {pendingRetentionFields} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";
import {newVoiceMappingRow, updateVoiceMappingRows, voiceMappingDraftError, voiceMappingDraftDirty} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";
import {captureConfigurationSubmission, retireReplacedConfigurationDraft} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";

function scenario({failRead = false, mode = "current"} = {}) {
  const f = fixture(), panel = f.create(), send = panel.hass.callWS;
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
      if (mode === "current") {
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
