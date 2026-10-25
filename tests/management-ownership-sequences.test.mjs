import assert from "node:assert/strict";
import test from "node:test";
import {deferred} from "./frontend-test-helpers.mjs";
import {fixture, edit, visit, replace, retire, flushUntil, baseConfig, saveConfiguration,
  confirmStateSafeNavigation, discardConfigurationDraft, restorePanelDrafts, saveConfigurationAcrossRestart,
  captureConfigurationSubmission, configurationSubmissionIsCurrent, reconcileConfigurationAcknowledgement} from "./management-ownership-harness.mjs";

// The expected authoring value is defined by user commands, not by the merge
// implementation. Every case runs the production request and lifecycle code.
for (const outcome of ["success", "failure", "cancel"]) for (const laterEdit of ["unchanged", "new", "revert", "title"])
  for (const readOrder of ["none", "before", "after", "before-failure", "after-failure"]) for (const boundary of ["stay", "navigate", "replace"]) {
    test(`O2/O3/O4/O5: write=${outcome}, edit=${laterEdit}, read=${readOrder}, boundary=${boundary}`, async t => {
      const {state, create, agentA}=fixture(), source=create();
      let active=source;
      edit(source, "prompt", "Submitted");
      const write=state.holdWrite(outcome), saving=saveConfiguration(source, null);
      let read, reading;
      t.after(async () => {write.release.resolve(); read?.release.resolve("success"); await saving; await reading; retire(source, active);});
      await flushUntil(() => write.entered);
      const expectedPrompt=laterEdit === "new" ? "Later" : laterEdit === "revert" ? "Original" : "Submitted";
      if (laterEdit === "new" || laterEdit === "revert") edit(source, "prompt", expectedPrompt);
      if (laterEdit === "title") edit(source, "__title", "Later title");
      assert.equal(await confirmStateSafeNavigation(source, null), false, "pending writes cannot be switched/discarded");
      assert.equal(discardConfigurationDraft(source), false);
      if (readOrder !== "none") {
        read=state.holdRead(); source._invalidateCleanConfiguration(source._agentId);
        reading=source._loadConfigDraft({preserveResult:true}).catch(error => error);
        await flushUntil(() => read.entered);
        if (readOrder.startsWith("before")) {read.release.resolve(readOrder.endsWith("failure") ? "failure" : "success"); await reading;}
      }
      if (boundary === "navigate") {
        assert.equal(await confirmStateSafeNavigation(source, "guide"), false, "in-flight mutation admission protects route changes too");
      }
      if (boundary === "replace") active=replace(source, create);
      write.release.resolve(); await saving;
      if (readOrder.startsWith("after")) {read.release.resolve(readOrder.endsWith("failure") ? "failure" : "success"); await reading;}
      if (readOrder.endsWith("failure")) assert.match((await reading).message, /Controlled read failure/);
      if (boundary === "navigate") {
        assert.equal(await confirmStateSafeNavigation(source, "guide"), true);
        visit(source, "guide", "getting-started");
      }
      if (boundary === "replace") {
        restorePanelDrafts(active);
        await flushUntil(() => !source._configDirty && !source._eocAcknowledgedConfiguration, "source ownership transfers after settlement");
      }
      assert.equal(active._draft.prompt, expectedPrompt, "latest authored prompt survives all completions");
      assert.equal(active._draftTitle, laterEdit === "title" ? "Later title" : "Saved A");
      assert.equal(state.snapshot(agentA).config.prompt, outcome === "success" ? "Submitted" : "Original");
      assert.equal(state.snapshot(agentA).title, "Saved A", "later title edits are not persisted by an older submission");
      assert.equal(state.writes.length, 1, "read/recovery never replays a write");
      assert.equal(active._configurationSaving || false, false);
      assert.deepEqual(Object.fromEntries(Object.entries(active._draft).filter(([key]) => key !== "prompt")), Object.fromEntries(Object.entries(baseConfig).filter(([key]) => key !== "prompt")));
      retire(source, active);
    });
  }

const transitions = {
  user(panel) {panel.hass={...panel.hass, user:{id:"different-user"}};},
  entry(panel) {panel._selectedAgent().entry_id="different-entry";},
  assistant(panel, create, agentB) {panel._agentId=agentB;},
  "clear-and-return"(panel) {
    const saved=structuredClone(panel._configData);
    panel._clearConfigDraft();
    panel._configData=saved; panel._draft=structuredClone(saved.config);
    panel._draftTitle=saved.title; panel._draftAgentId=panel._agentId;
  },
};

for (const [name, transition] of Object.entries(transitions)) {
  for (const phase of ["write", "read"]) test(`O1/O3: restart retry rejects retired ${name} during ${phase}`, async () => {
    const {state, create, agentA, agentB}=fixture(), panel=create();
    panel._configData.server_epoch="old-process";
    state.records.get(agentA).server_epoch="new-process";
    state.records.get(agentA).revision="r1";
    edit(panel, "prompt", "Submitted");
    const write=state.holdWrite("conflict"), read=phase === "read" ? state.holdRead() : null;
    const saving=saveConfigurationAcrossRestart(panel, {config:structuredClone(panel._draft), revision:"r0"}, structuredClone(panel._draft), panel._draftTitle);
    const rejected=assert.rejects(saving, /Configuration changed in another tab/);
    await flushUntil(() => write.entered);
    if (phase === "write") transition(panel, create, agentB);
    write.release.resolve();
    if (read) {
      await flushUntil(() => read.entered);
      transition(panel, create, agentB);
      read.release.resolve("success");
    }
    await rejected;
    assert.equal(state.writes.length, 1, "a retired owner cannot retry its write");
    assert.equal(state.snapshot(agentA).config.prompt, "Original");
    retire(panel);
  });

  test(`O1: retired ${name} cannot acknowledge another authoring owner`, () => {
    const {create, agentB}=fixture(), panel=create();
    edit(panel, "prompt", "Submitted");
    const submission=captureConfigurationSubmission(panel);
    transition(panel, create, agentB);
    const current=structuredClone({config:panel._configData, draft:panel._draft, title:panel._draftTitle});
    assert.equal(configurationSubmissionIsCurrent(panel, submission), false);
    assert.equal(reconcileConfigurationAcknowledgement(panel, {valid:true, revision:"obsolete", title:"Old response", config:{...baseConfig, prompt:"Submitted"}}, submission), false);
    assert.deepEqual({config:panel._configData, draft:panel._draft, title:panel._draftTitle}, current);
    retire(panel);
  });

  test(`O1/O3: queued ${name} cannot dispatch under a new owner`, async () => {
    const {state, create, agentB}=fixture(), panel=create();
    const operation=panel._call("configuration", "save", {config:{prompt:"Old queued mutation"}, revision:"r0"});
    transition(panel, create, agentB);
    await assert.rejects(operation, /changed|owner|draft/i);
    assert.equal(state.writes.length, 0);
    retire(panel);
  });

  test(`O1/O3: ${name} queue retirement cannot absorb a new owner's identical command`, async () => {
    const {state, create, agentB}=fixture(), panel=create(), barrier=deferred();
    panel._eocFunctionMutationTail=barrier.promise;
    const command={names:["example"], enabled:false};
    const old=panel._call("tools", "set_enabled", command);
    transition(panel, create, agentB);
    if (name === "assistant") {
      panel._configData=state.snapshot(agentB); panel._draft=structuredClone(panel._configData.config); panel._draftAgentId=agentB;
    }
    const next=panel._call("tools", "set_enabled", command);
    barrier.resolve();
    await assert.rejects(old, /changed|owner|draft/i);
    await next;
    assert.equal(state.writes.length, 1, "only the current owner's operation reaches the adapter");
    retire(panel);
  });
}

test("O3: restart retry still succeeds for the same owner and unchanged persisted baseline", async () => {
  const {state, create, agentA}=fixture(), panel=create();
  panel._configData.server_epoch="old-process";
  state.records.get(agentA).server_epoch="new-process";
  state.records.get(agentA).revision="r1";
  edit(panel, "prompt", "Submitted");
  const write=state.holdWrite("conflict");
  const saving=saveConfigurationAcrossRestart(panel, {config:structuredClone(panel._draft), revision:"r0"}, structuredClone(panel._draft), panel._draftTitle);
  await flushUntil(() => write.entered);
  write.release.resolve();
  await saving;
  assert.equal(state.writes.length, 2);
  assert.equal(state.snapshot(agentA).config.prompt, "Submitted");
  retire(panel);
});

for (const [name, transition] of Object.entries(transitions)) for (const outcome of ["success", "failure"]) test(`O1/O4: ${outcome} section read cannot publish to retired ${name}`, async () => {
  const {state, create, agentB}=fixture(), panel=create();
  panel._configDataStale=true;
  const read=state.holdRead(), reading=panel._loadSectionData();
  await flushUntil(() => read.entered);
  transition(panel, create, agentB);
  const error=panel._error, result=panel._result;
  read.release.resolve(outcome); await reading;
  assert.equal(panel._error, error, "obsolete errors must not overwrite the current owner's UI");
  assert.equal(panel._result, result, "obsolete results must not overwrite the current owner's UI");
  retire(panel);
});

for (const outcome of ["success", "failure"]) test(`O4: current section read still reports ${outcome}`, async () => {
  const {state, create}=fixture(), panel=create();
  panel._configDataStale=true;
  const read=state.holdRead(), reading=panel._loadSectionData();
  await flushUntil(() => read.entered);
  read.release.resolve(outcome); await reading;
  if (outcome === "failure") assert.match(panel._error, /Controlled read failure/);
  else assert.equal(panel._error, null);
  assert.equal(panel._busy, false);
  retire(panel);
});

for (const readKind of ["ordinary", "preserve-result"]) test(`O1/O4: ${readKind} read cannot publish across a user change`, async () => {
  const {state, create}=fixture(), panel=create();
  panel._configDataStale=true;
  const read=state.holdRead(), reading=panel._loadConfigDraft({preserveResult:readKind === "preserve-result"});
  await flushUntil(() => read.entered);
  panel.hass={...panel.hass, user:{id:"different-user"}};
  const epoch=panel._eocConfigurationReadEpoch;
  read.release.resolve("success"); await reading;
  assert.equal(panel._eocConfigurationReadEpoch, epoch, "a stale actor cannot certify configuration");
  assert.equal(panel._configDataStale, true);
  retire(panel);
});

test("O1/O4: clearing and hydrating the same cached snapshot does not revive an old read", async () => {
  const {state, create}=fixture(), panel=create(), baseline=panel._configData;
  panel._configDataStale=true;
  const read=state.holdRead(), reading=panel._loadConfigDraft();
  await flushUntil(() => read.entered);
  panel._clearConfigDraft();
  panel._configData=baseline; panel._draft=structuredClone(baseline.config);
  panel._draftTitle=baseline.title; panel._draftAgentId=panel._agentId;
  const epoch=panel._eocConfigurationReadEpoch;
  read.release.resolve("success"); await reading;
  assert.equal(panel._eocConfigurationReadEpoch, epoch);
  retire(panel);
});

for (const projection of ["full", "retention"]) for (const readOutcome of ["success", "failure"])
  for (const replacements of [1,2]) for (const targetEdit of [false,true]) for (const sourceHistory of ["fresh","returned"]) {
    test(`O4/O5: acknowledged recovery projection=${projection}, read=${readOutcome}, replacements=${replacements}, targetEdit=${targetEdit}, history=${sourceHistory}`, async t => {
      const {state, create}=fixture(), source=create();
      if (sourceHistory === "returned") transitions["clear-and-return"](source);
      edit(source, "prompt", "Submitted");
      const write=state.holdWrite("success"), saving=saveConfiguration(source, null);
      let target, read;
      t.after(async () => {write.release.resolve(); read?.release.resolve("success"); await saving; retire(source, ...(target ? [target] : []));});
      await flushUntil(() => write.entered);
      edit(source, "prompt", "Later source edit");
      target=replace(source, create, projection);
      if (replacements === 2) target=replace(target, create, projection);
      read=state.holdRead();
      write.release.resolve(); await saving;
      restorePanelDrafts(target);
      await flushUntil(() => read.entered);
      if (targetEdit) edit(target, "usage_request_retention_days", 7);
      read.release.resolve(readOutcome);
      if (readOutcome === "failure") {
        await flushUntil(() => target.toasts.length > 0);
        assert.equal(source._configDirty, true, "failed certification keeps the source owned");
        assert.equal(source._draft.prompt, "Later source edit");
        visit(target, "assistant", "basics");
        restorePanelDrafts(target);
      }
      await flushUntil(() => !source._configDirty && !source._eocAcknowledgedConfiguration, "acknowledged source transfers even if destination was edited");
      assert.equal(target._draft.prompt, "Later source edit");
      assert.equal(target._draft.usage_request_retention_days, targetEdit ? 7 : 30);
      assert.equal(target._configData.config.prompt, "Submitted");
      assert.notEqual(target._configData.projection, "retention");
      assert.equal(state.writes.length, 1);
      retire(source, target);
    });
  }
