import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery, saveConfiguration, discardConfigurationDraft, flushUntil} from "./management-ownership-harness.mjs";
import {voiceMappingDraft, updateVoiceMappingRows, newVoiceMappingRow, voiceMappingDraftDirty} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

for (const reader of ["route", "ordinary", "preserve-result"]) for (const decision of ["save", "discard"]) {
  test(`a retired ${reader} read cannot revive transferred configuration after destination ${decision}`, async t => {
    const f = fixture();
    for (const record of f.state.records.values()) record.config.voice_device_mappings = {office:"user:owner"};
    const source = f.create(), target = f.create();
    let reading, gate;
    t.after(async () => {gate?.release.resolve("success"); await reading; discardConfigurationDraft(source); discardConfigurationDraft(target); retire(source, target);});
    updateVoiceMappingRows(source, [{...newVoiceMappingRow(), deviceId:"living", owner:"shared:household"}]);
    source._draft.prompt = "Transferred source prompt";
    source._syncConfigDirty();
    updateVoiceMappingRows(target, [...voiceMappingDraft(target).rows, newVoiceMappingRow()]);
    target._syncConfigDirty();
    source._invalidateCleanConfiguration(source._agentId);
    gate = f.state.holdRead();
    reading = reader === "route" ? source._loadSectionData(true) : source._loadConfigDraft({preserveResult:reader === "preserve-result"});
    await flushUntil(() => gate.entered);
    source.isConnected = false;
    retainPanelDrafts(source);
    restorePanelDrafts(target);
    stopDraftRecovery(target);
    assert.equal(source._configDirty, false);
    assert.equal(voiceMappingDraftDirty(source), true);
    assert.equal(target._draft.prompt, "Transferred source prompt");
    if (decision === "save") {
      target._draft.prompt = "New saved destination prompt";
      updateVoiceMappingRows(target, [{...newVoiceMappingRow(), deviceId:"bedroom", owner:"unretained"}]);
      target._syncConfigDirty();
      await saveConfiguration(target, null);
    } else discardConfigurationDraft(target);
    gate.release.resolve("success");
    await reading;
    restorePanelDrafts(target);
    stopDraftRecovery(target);
    assert.equal(target._draft.prompt, decision === "save" ? "New saved destination prompt" : "Original");
    assert.equal(source._configDirty, false, "an old read cannot resurrect consumed serialized values");
    assert.equal(f.state.writes.length, decision === "save" ? 1 : 0);
  });
}

for (const preserveResult of [false, true]) for (const reconnect of [false, true]) {
  test(`a current configuration read preserves newer edits: preserve=${preserveResult}, reconnect=${reconnect}`, async t => {
    const f = fixture(), panel = f.create();
    let reading, gate;
    t.after(async () => {gate?.release.resolve("success"); await reading; discardConfigurationDraft(panel); retire(panel);});
    f.state.records.get(panel._agentId).config.max_tokens = 200;
    panel._invalidateCleanConfiguration(panel._agentId);
    gate = f.state.holdRead();
    reading = panel._loadConfigDraft({preserveResult});
    await flushUntil(() => gate.entered);
    panel._draft.prompt = "Newer authoring";
    panel._syncConfigDirty();
    if (reconnect) panel.isConnected = false;
    gate.release.resolve("success");
    await reading;
    if (reconnect) {
      assert.equal(panel._configData.config.max_tokens, 100, "the detached read cannot publish a baseline");
      assert.equal(panel._configDataStale, true);
      assert.equal(panel._draft.prompt, "Newer authoring");
      panel.isConnected = true;
      await panel._loadConfigDraft({preserveResult});
    }
    assert.equal(panel._configData.config.max_tokens, 200);
    assert.equal(panel._draft.max_tokens, 200);
    assert.equal(panel._draft.prompt, "Newer authoring");
    assert.equal(panel._configDirty, true);
    assert.equal(f.state.writes.length, 0, "a fresh read never retries a write");
  });
}
