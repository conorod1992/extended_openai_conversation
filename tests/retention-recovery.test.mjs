import test from "node:test";
import assert from "node:assert/strict";
import {fixture, edit, retire, replace, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery, configurationSubmissionIsCurrent, saveConfiguration, discardConfigurationDraft, flushUntil} from "./management-ownership-harness.mjs";

function failedRetention() {
  const scenario = fixture(), panel = scenario.create();
  const send = panel.hass.callWS;
  let fail = true;
  panel.hass.callWS = async message => {
    const result = await send(message);
    if (message.action === "save" && fail) {
      fail = false;
      throw {code:"eoai_mutation_committed", message:"Configuration saved, but retention deletion failed; retry saving retention."};
    }
    return result;
  };
  edit(panel, "usage_request_retention_days", 0);
  return {...scenario, panel};
}

test("committed retention failure refreshes revision but keeps deletion available to Save", async () => {
  const {panel, state} = failedRetention();
  try {
    await saveConfiguration(panel, null);
    assert.equal(panel._configData.revision, "r1");
    assert.equal(panel._configDirty, true);
    assert.ok(panel._eocDirtyConfigKeys.has("usage_request_retention_days"));
    panel._draftTitle = "Later title";
    await saveConfiguration(panel, null);
    assert.equal(state.writes[1].revision, "r1");
    assert.deepEqual(state.writes[1].config, {usage_request_retention_days:0});
    assert.equal(state.writes[1].title, "Later title");
    assert.equal(panel._configDirty, false);
    assert.equal(panel._eocPendingRetention, null);
  } finally { retire(panel); }
});

test("a failed refresh can be retried while newer edits survive the eventual read", async () => {
  const {panel, state} = failedRetention();
  try {
    const failedRead = state.holdRead();
    const first = saveConfiguration(panel, null);
    await flushUntil(() => failedRead.entered);
    edit(panel, "temperature", 0.8);
    failedRead.release.resolve("failure");
    await first;
    assert.equal(panel._configDataStale, true);
    await saveConfiguration(panel, null);
    assert.equal(state.writes[1].revision, "r1");
    assert.deepEqual(state.writes[1].config, {temperature:0.8, usage_request_retention_days:0});
    assert.equal(panel._draft.temperature, 0.8);
    assert.equal(panel._configDirty, false);
  } finally { retire(panel); }
});

test("discarding later edits keeps the committed retention deletion retry", async () => {
  const {panel, state} = failedRetention();
  try {
    await saveConfiguration(panel, null);
    edit(panel, "temperature", 0.8);
    assert.equal(discardConfigurationDraft(panel), true);
    assert.equal(panel._draft.temperature, 0.4);
    assert.equal(panel._configDirty, true);
    await saveConfiguration(panel, null);
    assert.deepEqual(state.writes[1].config, {usage_request_retention_days:0});
    assert.equal(panel._configDirty, false);
  } finally { retire(panel); }
});

for (const transition of ["selection", "authentication", "draft"]) {
  test(`retention recovery cannot acquire a newer ${transition} owner`, async () => {
    const {panel, state, agentB} = failedRetention();
    try {
      const read = state.holdRead();
      const saving = saveConfiguration(panel, null);
      await flushUntil(() => read.entered);
      if (transition === "selection") panel._agentId = agentB;
      else if (transition === "authentication") panel._eocAuthentication = {userId:panel.hass.user.id, generation:panel._eocAuthenticationGeneration + 1};
      else panel._eocDraftGeneration = (panel._eocDraftGeneration || 0) + 1;
      const current = panel._configData;
      read.release.resolve("success");
      await saving;
      assert.equal(panel._configData, current);
      panel._syncConfigDirty();
      assert.equal(state.writes.length, 1);
      assert.equal(panel.toasts.length, 0);
    } finally { retire(panel); }
  });
}

test("settled deletion recovery survives repeated panel replacement with a fresh owner and newer edits", async () => {
  const {panel:source, create, state} = failedRetention();
  const panels = [source];
  try {
    await saveConfiguration(source, null);
    let panel = source;
    for (let generation = 1; generation <= 2; generation++) {
      const previous = panel;
      panel = replace(previous, (...args) => {
        const target = create(...args);
        target._eocDraftGeneration = generation + 5;
        target._eocAuthentication = {userId:target.hass.user.id, generation:generation + 10};
        edit(target, "temperature", 0.8);
        return target;
      });
      panels.push(panel);
      assert.equal(previous._configDirty, false);
      assert.equal(previous._eocPendingRetention, null);
      assert.equal(panel._configDirty, true);
      assert.equal(panel._draft.temperature, 0.8);
      assert.equal(configurationSubmissionIsCurrent(panel, panel._eocPendingRetention), true);
    }
    await saveConfiguration(panel, null);
    assert.deepEqual(state.writes[1].config, {temperature:0.8, usage_request_retention_days:0});
    assert.equal(state.writes[1].revision, "r1");
    assert.equal(panel._configDirty, false);
    assert.equal(panel._eocPendingRetention, null);
  } finally { retire(...panels); }
});

for (const boundary of ["user", "entry", "assistant", "access", "authentication", "draft"]) {
  test(`replacement cannot transfer pending deletion across a changed ${boundary}`, async () => {
    const {panel:source, create, agentB} = failedRetention();
    let target;
    try {
      await saveConfiguration(source, null);
      if (boundary === "authentication") source._eocAuthentication = {userId:source.hass.user.id, generation:1};
      if (boundary === "draft") source._eocDraftGeneration = 1;
      source.isConnected = false;
      retainPanelDrafts(source);
      stopDraftRecovery(source);
      target = create(boundary === "assistant" ? agentB : source._agentId,
        boundary === "user" ? "another-user" : source.hass.user.id);
      if (boundary === "entry") target._data.agents[0].entry_id = "another-entry";
      if (boundary === "access") target._data.is_admin = false;
      restorePanelDrafts(target);
      stopDraftRecovery(target);
      assert.equal(target._eocPendingRetention ?? null, null);
      assert.equal(target._configDirty, false);
    } finally { retire(...[source, target].filter(Boolean)); }
  });
}
