import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, saveConfiguration, discardConfigurationDraft, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery} from "./management-ownership-harness.mjs";
import {voiceMappingDraft, updateVoiceMappingRows, newVoiceMappingRow, voiceMappingDraftDirty, captureVoiceMappingDraft} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

function setup() {
  const f = fixture();
  for (const record of f.state.records.values()) record.config.voice_device_mappings = {office:"user:owner"};
  const source = f.create(), target = f.create();
  updateVoiceMappingRows(source, [{...newVoiceMappingRow(), deviceId:"living", owner:"shared:household"}]);
  source._syncConfigDirty();
  updateVoiceMappingRows(target, [...voiceMappingDraft(target).rows, newVoiceMappingRow()]);
  target._syncConfigDirty();
  source.isConnected = false;
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  stopDraftRecovery(target);
  assert.equal(source._configDirty, false);
  assert.equal(voiceMappingDraftDirty(source), true);
  return {...f, source, target};
}

for (const saved of [{bedroom:"unretained"}, {office:"user:owner"}]) for (const replacement of [false, true]) {
  test(`a saved destination keeps deferred Voice rows for review: same policy=${Boolean(saved.office)}, replacement=${replacement}`, async () => {
    const f = setup(), {source, target} = f;
    let active = target;
    try {
      const recovered = captureVoiceMappingDraft(source);
      updateVoiceMappingRows(target, Object.entries(saved).map(([deviceId, owner]) => ({...newVoiceMappingRow(), deviceId, owner})));
      target._draftTitle = "Newly saved destination";
      target._syncConfigDirty();
      await saveConfiguration(target, null);
      assert.deepEqual(f.state.snapshot(target._agentId).config.voice_device_mappings, saved);
      if (replacement) {
        target.isConnected = false;
        retainPanelDrafts(target);
        active = f.create();
      }
      restorePanelDrafts(active);
      stopDraftRecovery(active);
      assert.deepEqual(active._draft.voice_device_mappings, saved);
      assert.equal(active._draftTitle, "Newly saved destination");
      assert.equal(voiceMappingDraftDirty(source), true, "retained rows remain available for review");
      assert.deepEqual(captureVoiceMappingDraft(source), recovered);
      assert.equal(f.state.writes.length, 1, "recovery does not replay a save");
    } finally { retire(source, target, active); }
  });
}

test("Voice scope identity survives rendering and preserves invalid rows in its review projection", () => {
  const {source, target} = setup();
  try {
    const state = voiceMappingDraft(target);
    const scope = [...target._unsavedState.scopes.entries()].find(([id]) => id.startsWith("voice-mapping-rows:"))[1];
    voiceMappingDraft(target);
    assert.equal([...target._unsavedState.scopes.entries()].find(([id]) => id.startsWith("voice-mapping-rows:"))[1], scope);
    assert.deepEqual(structuredClone(state), state, "authoring values stay independent of callback registrations");
    assert.equal(scope.isCurrent(), true);
    assert.equal(scope.read().length, 2);
    assert.equal(scope.read()[1].deviceId, "");
    assert.equal(Object.hasOwn(scope.read()[0], "id"), false);
    target._clearConfigDraft();
    assert.equal(scope.isCurrent(), false);
  } finally { retire(source, target); }
});

test("an unsaved destination Discard still receives deferred Voice rows", () => {
  const {source, target} = setup();
  try {
    discardConfigurationDraft(target);
    restorePanelDrafts(target);
    assert.deepEqual(target._draft.voice_device_mappings, {living:"shared:household"});
    assert.equal(voiceMappingDraftDirty(source), false);
  } finally { retire(source, target); }
});


test("a resumed source save retires its earlier deferred recovery decision", async () => {
  const f = setup(), {source, target} = f;
  let reopened;
  try {
    source.isConnected = true;
    source._syncConfigDirty();
    await saveConfiguration(source, null);
    assert.equal(voiceMappingDraftDirty(source), false);
    updateVoiceMappingRows(source, [{...newVoiceMappingRow(), deviceId:"hallway", owner:"unretained"}]);
    source._syncConfigDirty();
    source.isConnected = false;
    retainPanelDrafts(source);
    reopened = f.create();
    restorePanelDrafts(reopened);
    assert.deepEqual(reopened._draft.voice_device_mappings, {hallway:"unretained"});
    assert.equal(voiceMappingDraftDirty(source), false);
  } finally { retire(source, target, ...(reopened ? [reopened] : [])); }
});
