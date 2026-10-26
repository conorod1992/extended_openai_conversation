import assert from "node:assert/strict";
import test from "node:test";
import {fixture, edit, retire, flushUntil, saveConfiguration, captureConfigurationSubmission, configurationSubmissionIsCurrent} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";

function changeUserAndReturn(panel, intermediate = "other-user") {
  const original = panel.hass;
  panel.hass = {...original, user:intermediate == null ? undefined : {id:intermediate}};
  panel.hass = original;
}

for (const intermediate of ["other-user", null]) {
  test(`a queued operation cannot regain authority after user A → ${intermediate ?? "unknown"} → A`, async t => {
    const {state, create} = fixture(), panel = create(), barrier = deferred();
    t.after(() => {barrier.resolve(); retire(panel);});
    edit(panel, "prompt", "Retained authoring");
    const draft = panel._draft, baseline = panel._configData, submission = captureConfigurationSubmission(panel);
    panel._eocFunctionMutationTail = barrier.promise;
    const operation = panel._call("tools", "set_enabled", {names:["example"], enabled:false});
    const rejected = assert.rejects(operation, /changed|owner|draft/i);
    changeUserAndReturn(panel, intermediate);
    assert.equal(configurationSubmissionIsCurrent(panel, submission), false);
    assert.equal(panel._draft, draft);
    assert.equal(panel._configData, baseline);
    assert.equal(panel._draft.prompt, "Retained authoring");
    barrier.resolve(); await rejected;
    assert.equal(state.writes.length, 0);
  });
}

test("a fresh identical command is not deduplicated into the retired authentication operation", async t => {
  const {state, create} = fixture(), panel = create(), barrier = deferred();
  t.after(() => {barrier.resolve(); retire(panel);});
  panel._eocFunctionMutationTail = barrier.promise;
  const command = {names:["example"], enabled:false};
  const old = panel._call("tools", "set_enabled", command);
  const rejected = assert.rejects(old, /changed|owner|draft/i);
  changeUserAndReturn(panel);
  const fresh = panel._call("tools", "set_enabled", command);
  barrier.resolve(); await rejected; await fresh;
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].subentry_id, panel._agentId);
  assert.equal(panel._pendingMutations.size, 0);
});

test("same-user Home Assistant updates preserve pending operation ownership and authored values", async t => {
  const {state, create} = fixture(), panel = create(), barrier = deferred();
  t.after(() => {barrier.resolve(); retire(panel);});
  edit(panel, "prompt", "Still editing");
  const draft = panel._draft, submission = captureConfigurationSubmission(panel);
  panel._eocFunctionMutationTail = barrier.promise;
  const operation = panel._call("tools", "set_enabled", {names:["example"], enabled:false});
  for (let n = 0; n < 4; n++) panel.hass = {...panel.hass, states:{[`sensor.update_${n}`]:{state:String(n)}}};
  assert.equal(configurationSubmissionIsCurrent(panel, submission), true);
  assert.equal(panel._draft, draft);
  assert.equal(draft.prompt, "Still editing");
  barrier.resolve(); await operation;
  assert.equal(state.writes.length, 1);
});

for (const outcome of ["success", "failure"]) {
  test(`an old ${outcome} read cannot publish after an authenticated-user round trip`, async t => {
    const {state, create} = fixture(), panel = create();
    panel._configDataStale = true;
    const gate = state.holdRead(), reading = panel._loadSectionData();
    t.after(async () => {gate.release.resolve("success"); await reading; retire(panel);});
    await flushUntil(() => gate.entered);
    const result = panel._result, error = panel._error, draft = panel._draft;
    changeUserAndReturn(panel);
    gate.release.resolve(outcome); await reading;
    assert.equal(panel._result, result);
    assert.equal(panel._error, error);
    assert.equal(panel._draft, draft);
    assert.equal(panel._configDataStale, true);
  });
}

for (const failed of [false, true]) {
  test(`a dispatched configuration save keeps its backend outcome without reviving publication, failed=${failed}`, async t => {
    const {state, create, agentA} = fixture(), panel = create();
    edit(panel, "prompt", "Submitted before identity change");
    const gate = state.holdWrite(failed ? "failure" : "success"), saving = saveConfiguration(panel);
    t.after(async () => {gate.release.resolve(); await saving; retire(panel);});
    await flushUntil(() => gate.entered);
    changeUserAndReturn(panel);
    edit(panel, "prompt", "Later authoring after return");
    const draft = panel._draft, baseline = panel._configData, result = panel._result;
    gate.release.resolve(); await saving;
    assert.equal(state.snapshot(agentA).config.prompt, failed ? "Original" : "Submitted before identity change");
    assert.equal(state.writes.length, 1);
    assert.equal(panel._draft, draft);
    assert.equal(draft.prompt, "Later authoring after return");
    assert.equal(panel._configData, baseline);
    assert.equal(panel._result, result);
    assert.equal(panel._configurationSaving, false);
    assert.deepEqual(panel.toasts, [], "a retired result must not announce success or failure to the new operation session");
  });
}

const policies = [
  {kind:"Guest", view:"capabilities/guest-mode", field:"_guestDraft",
    initial:{config:{guest_web_search:false}, revision:"one", legacy_policy:false},
    saved:{config:{guest_web_search:true, guest_policy_version:2}, revision:"two"},
    submitted:panel => {panel._guestDraft.guest_web_search = true;},
    later:panel => {panel._guestDraft.guest_web_search = false;}},
  {kind:"Quiet Hours", view:"capabilities/quiet-hours", field:"_quietHoursDraft",
    initial:{config:{enabled:false, start:"22:00"}, revision:"one"},
    saved:{config:{enabled:true, start:"22:00", wake_sound:"off"}, revision:"two"},
    submitted:panel => {panel._quietHoursDraft.enabled = true;},
    later:panel => {panel._quietHoursDraft.enabled = false;}},
  {kind:"Request Rules", view:"capabilities/request-rules", field:"_rulesSettingsDraft",
    initial:{defaults:{fuzzy:false}, wording_groups:[], revision:"one"},
    saved:{defaults:{fuzzy:true, word_forms:true}, wording_groups:[], revision:"two"},
    submitted:panel => {panel._rulesSettingsDraft.defaults.fuzzy = true;},
    later:panel => {panel._rulesSettingsDraft.defaults.fuzzy = false;}},
];

function policyPanel(policy) {
  const {create} = fixture(), panel = create();
  [panel._page, panel._subsection] = policy.view.split("/");
  panel._result = structuredClone(policy.initial); panel._busy = false;
  initializePageDraft(panel); policy.submitted(panel);
  return panel;
}

for (const policy of policies) {
  test(`${policy.kind}: an authored scope and its unsaved edits survive authentication round trips`, t => {
    const panel = policyPanel(policy); t.after(() => retire(panel));
    const scope = currentPageScope(panel), draft = panel[policy.field], baseline = scope.baseline;
    changeUserAndReturn(panel);
    initializePageDraft(panel);
    assert.equal(currentPageScope(panel), scope);
    assert.equal(scope.isCurrent(), true);
    assert.equal(panel[policy.field], draft);
    assert.equal(scope.baseline, baseline);
    assert.equal(scope.dirty(), true);
  });

  for (const failed of [false, true]) for (const later of [false, true]) {
    test(`${policy.kind}: retired save settles without publishing after user ABA, failed=${failed}, later=${later}`, async t => {
      const panel = policyPanel(policy), gate = deferred(), calls = [];
      const saved = structuredClone(policy.saved);
      panel._hass.callWS = async request => {calls.push(structuredClone(request)); return gate.promise;};
      const scope = currentPageScope(panel), originalBaseline = structuredClone(scope.baseline);
      const saving = scope.save();
      const rejected = failed ? assert.rejects(saving, /response lost/) : null;
      t.after(async () => {gate.resolve(saved); await saving.catch(() => {}); retire(panel);});
      await flushUntil(() => calls.length === 1);
      changeUserAndReturn(panel);
      if (later) policy.later(panel);
      const draft = panel[policy.field], authored = structuredClone(draft), result = panel._result;
      if (failed) gate.reject(Error("response lost")); else gate.resolve(saved);
      if (failed) await rejected; else await saving;
      assert.equal(currentPageScope(panel), scope);
      assert.equal(scope.isCurrent(), true, "authoring ownership survives operation retirement");
      assert.equal(panel[policy.field], draft, "an obsolete operation cannot replace the authored container");
      assert.deepEqual(panel[policy.field], authored);
      assert.equal(panel._result, result);
      assert.deepEqual(scope.baseline, failed ? originalBaseline : saved.config || {defaults:saved.defaults, wording_groups:saved.wording_groups});
      assert.equal(scope.revision, failed ? "one" : "two");
      assert.equal(scope.pending, false);
      assert.equal(calls.length, 1, "settlement does not replay the write or perform a refresh");
      if (!failed) {
        scope.discard();
        const accepted = result.config || {defaults:result.defaults, wording_groups:result.wording_groups};
        assert.deepEqual(panel[policy.field], accepted, "a retired response cannot replace the accepted Discard projection");
        panel._result = structuredClone(saved);
        initializePageDraft(panel);
        assert.deepEqual(panel[policy.field], saved.config || {defaults:saved.defaults, wording_groups:saved.wording_groups}, "a fresh accepted current-owner read supplies the saved values");
      }
    });
  }
}
