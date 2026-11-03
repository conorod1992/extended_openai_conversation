import test from "node:test";
import assert from "node:assert/strict";
import {fixture, retire, saveConfiguration, discardConfigurationDraft, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery, flushUntil} from "./management-ownership-harness.mjs";
import {voiceMappingDraft, updateVoiceMappingRows, voiceMappingDraftError, voiceMappingDraftDirty, transferVoiceMappingDraft, newVoiceMappingRow} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

const saved = {office:"user:owner"}, recovered = {living:"shared:household"};
function setup() {
  const f = fixture();
  for (const record of f.state.records.values()) record.config.voice_device_mappings = structuredClone(saved);
  const source = f.create(), target = f.create();
  updateVoiceMappingRows(source, [{...newVoiceMappingRow(), deviceId:"living", owner:"shared:household"}]);
  source._syncConfigDirty();
  updateVoiceMappingRows(target, [...voiceMappingDraft(target).rows, newVoiceMappingRow()]);
  target._syncConfigDirty();
  source.isConnected = false;
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  stopDraftRecovery(target);
  assert.equal(source._configDirty, false, "the serialized configuration has already transferred");
  assert.equal(voiceMappingDraftDirty(source), true, "invalid destination rows defer the independent Voice scope");
  assert.ok(voiceMappingDraftError(target));
  return {...f, source, target};
}

test("deferred Voice rows recover after Discard and Save persists the displayed assignments", async () => {
  const f = setup(), {source, target} = f;
  try {
    discardConfigurationDraft(target);
    restorePanelDrafts(target);
    assert.deepEqual(voiceMappingDraft(target).rows.map(({deviceId, owner}) => ({deviceId, owner})), [{deviceId:"living", owner:"shared:household"}]);
    assert.deepEqual(target._draft.voice_device_mappings, recovered);
    assert.equal(voiceMappingDraftDirty(source), false);
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {voice_device_mappings:recovered});
    assert.deepEqual(f.state.snapshot(target._agentId).config.voice_device_mappings, recovered);
    assert.equal(target._configDirty, false);
    assert.equal(voiceMappingDraftDirty(target), false);
  } finally { retire(source, target); }
});

test("a deferred Voice scope cannot overwrite a separately authored persistence mapping", () => {
  const {source, target} = setup();
  try {
    discardConfigurationDraft(target);
    target._draft.voice_device_mappings = {bedroom:"unretained"};
    assert.equal(transferVoiceMappingDraft(target, source), false);
    assert.deepEqual(target._draft.voice_device_mappings, {bedroom:"unretained"});
    assert.equal(voiceMappingDraftDirty(source), true);
    discardConfigurationDraft(target);
    restorePanelDrafts(target);
    assert.deepEqual(target._draft.voice_device_mappings, recovered);
  } finally { retire(source, target); }
});

for (const missing of ["draft", "owner"]) test(`a deferred Voice scope waits for the destination configuration: ${missing}`, () => {
  const {source, target} = setup();
  try {
    discardConfigurationDraft(target);
    if (missing === "draft") target._draft = null;
    else target._draftAgentId = "other-assistant";
    assert.equal(transferVoiceMappingDraft(target, source), false);
    assert.equal(voiceMappingDraftDirty(source), true);
    target._draft = structuredClone(target._configData.config);
    target._draftAgentId = target._agentId;
    restorePanelDrafts(target);
    assert.deepEqual(target._draft.voice_device_mappings, recovered);
  } finally { retire(source, target); }
});

test("row-only recovery loads a complete Retention baseline before receiving Voice assignments", async () => {
  const f = setup(), {source, target} = f;
  try {
    discardConfigurationDraft(target);
    target._page = "usage-maintenance"; target._subsection = "retention";
    target._configData = {...target._configData, projection:"retention", config:{usage_request_retention_days:30}};
    target._draft = {usage_request_retention_days:30};
    const read = f.state.holdRead();
    restorePanelDrafts(target);
    await flushUntil(() => read.entered);
    assert.equal(voiceMappingDraftDirty(source), true);
    assert.equal(target._draft.voice_device_mappings, undefined);
    read.release.resolve("success");
    await flushUntil(() => !voiceMappingDraftDirty(source));
    assert.deepEqual(target._draft.voice_device_mappings, recovered);
    await saveConfiguration(target, null);
    assert.deepEqual(f.state.writes[0].config, {voice_device_mappings:recovered});
    assert.equal(f.state.writes[0].revision, "r0");
  } finally { retire(source, target); }
});

test("deferred invalid Voice rows stay invalid and block Save after recovery", async () => {
  const f = setup(), {source, target} = f;
  try {
    updateVoiceMappingRows(source, [...voiceMappingDraft(source).rows, newVoiceMappingRow()]);
    discardConfigurationDraft(target);
    restorePanelDrafts(target);
    assert.ok(voiceMappingDraftError(target));
    assert.deepEqual(target._draft.voice_device_mappings, saved);
    await saveConfiguration(target, null);
    assert.equal(f.state.writes.length, 0);
  } finally { retire(source, target); }
});

for (const committed of [false, true]) for (const rows of ["assignment", "removal", "empty"]) test(`Voice recovery adopts the destination baseline after a lost reply: committed=${committed}, rows=${rows}`, async () => {
  const f = fixture();
  for (const record of f.state.records.values()) record.config.voice_device_mappings = {office:"user:owner", living:"shared:household"};
  const source = f.create(), send = source.hass.callWS;
  const authored = rows === "assignment" ? {office:"unretained", living:"shared:household"} : rows === "removal" ? saved : {};
  updateVoiceMappingRows(source, Object.entries(authored).map(([deviceId, owner]) => ({...newVoiceMappingRow(), deviceId, owner})));
  source._syncConfigDirty();
  source.hass.callWS = async request => {
    if (request.section === "configuration" && request.action === "save") {
      if (committed) await send(request);
      throw new Error("Save reply lost");
    }
    return send(request);
  };
  await saveConfiguration(source, null);
  assert.equal(source._configDirty, true);
  const target = f.create(), ids = voiceMappingDraft(target).rows.map(row => row.id);
  source.isConnected = false; retainPanelDrafts(source); restorePanelDrafts(target);
  try {
    assert.deepEqual(target._draft.voice_device_mappings, authored);
    assert.equal(target._configDirty, !committed);
    assert.equal(voiceMappingDraftDirty(target), !committed);
    assert.equal(voiceMappingDraftDirty(source), false);
    if (committed) {
      assert.deepEqual(voiceMappingDraft(target).rows.map(row => row.id), ids, "equal rows keep the native widgets' IDs");
      const writer = f.create();
      try {
        updateVoiceMappingRows(writer, [{...newVoiceMappingRow(), deviceId:"hall", owner:"unretained"}]); writer._syncConfigDirty();
        await saveConfiguration(writer, null);
        target._invalidateCleanConfiguration(target._agentId);
        await target._loadConfigDraft({preserveResult:true, full:true});
        assert.deepEqual(target._draft.voice_device_mappings, {hall:"unretained"});
        assert.deepEqual(voiceMappingDraft(target).rows.map(({deviceId, owner}) => ({deviceId, owner})), [{deviceId:"hall", owner:"unretained"}]);
        assert.equal(voiceMappingDraftDirty(target), false, "a later read cannot retain an obsolete authoring baseline");
        assert.equal(target._configDirty, false);
      } finally { retire(writer); }
    } else {
      await saveConfiguration(target, null);
      assert.deepEqual(f.state.snapshot(target._agentId).config.voice_device_mappings, authored);
      assert.equal(target._configDirty, false);
    }
  } finally { retire(source, target); }
});
