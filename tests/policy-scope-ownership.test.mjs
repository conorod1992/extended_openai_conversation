import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, flushUntil} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";

const policies = [
  {kind:"guest", view:"capabilities/guest-mode", field:"_guestDraft", section:"guest_mode", action:"save_policy",
    initial:{config:{guest_web_search:false}, revision:"source-1", legacy_policy:false},
    target:{config:{guest_web_search:true}, revision:"target-1", legacy_policy:false},
    saved:{config:{guest_web_search:true, guest_policy_version:2}, revision:"source-2"},
    edit:p => {p._guestDraft.guest_web_search=true;}},
  {kind:"quiet", view:"capabilities/quiet-hours", field:"_quietHoursDraft", section:"quiet_hours", action:"update",
    initial:{config:{enabled:false, start:"22:00"}, revision:"source-1"},
    target:{config:{enabled:true, start:"22:00"}, revision:"target-1"},
    saved:{config:{enabled:true, start:"22:00", wake_sound:"off"}, revision:"source-2"},
    edit:p => {p._quietHoursDraft.enabled=true;}},
  {kind:"rules", view:"capabilities/request-rules", field:"_rulesSettingsDraft", section:"request_rules", action:"settings",
    initial:{defaults:{fuzzy:false}, wording_groups:[], revision:"source-1"},
    target:{defaults:{fuzzy:true}, wording_groups:[], revision:"target-1"},
    saved:{defaults:{fuzzy:true, word_forms:true}, wording_groups:[], revision:"source-2"},
    edit:p => {p._rulesSettingsDraft.defaults.fuzzy=true;}},
];

function setup(policy, anonymous = false) {
  const harness=fixture(), panel=harness.create();
  if(anonymous) panel.hass={...panel.hass,user:undefined};
  [panel._page,panel._subsection]=policy.view.split("/");
  panel._result=structuredClone(policy.initial);panel._busy=false;
  initializePageDraft(panel);policy.edit(panel);
  return {panel,...harness};
}

function transition(panel, kind, agentB) {
  if(kind === "user") panel.hass={...panel.hass,user:{id:"different-user"}};
  if(kind === "entry") panel._selectedAgent().entry_id="different-provider";
  if(kind === "session") panel._clearConfigDraft();
  if(kind === "agent-roundtrip") {
    const agent=panel._agentId;panel._agentId=agentB;panel._clearConfigDraft();panel._agentId=agent;
  }
  if(kind === "user-roundtrip") {
    const user=panel.hass.user;panel.hass={...panel.hass,user:{id:"different-user"}};
    panel._clearConfigDraft();panel.hass={...panel.hass,user};
  }
}

for(const policy of policies) for(const change of ["user","entry","session","agent-roundtrip","user-roundtrip"])
  for(const replace of [false,true]) for(const failed of [false,true]) {
    test(`${policy.kind}: ${change}, replace=${replace}, failed=${failed} retires scope publication`, async () => {
      const {panel,agentB}=setup(policy), gate=deferred(), writes=[];
      panel._hass.callWS=async request => {writes.push(structuredClone(request));return gate.promise;};
      const source=currentPageScope(panel), original=structuredClone(source.baseline), pending=source.save();
      await flushUntil(() => writes.length === 1);
      transition(panel,change,agentB);
      if(replace) panel._unsavedState.scopes.delete(policy.view);
      panel._result=structuredClone(policy.target);initializePageDraft(panel);
      const target=currentPageScope(panel), draft=panel[policy.field];
      const expectedDraft=structuredClone(draft), baseline=structuredClone(target.baseline), revision=target.revision;
      const rejection=failed ? assert.rejects(pending,/response lost/) : null;
      if(failed) gate.reject(Error("response lost"));else gate.resolve(structuredClone(policy.saved));
      if(failed) await rejection;else await pending;
      assert.notEqual(target,source,"same assistant ID cannot retain a retired owner/session");
      assert.equal(panel[policy.field],draft,"late settlement must not replace target-owned draft containers");
      assert.deepEqual(panel[policy.field],expectedDraft);
      assert.deepEqual(panel._result,policy.target);
      assert.deepEqual(target.baseline,baseline);assert.equal(target.revision,revision);
      assert.deepEqual(source.baseline,failed ? original : policy.saved.config || {defaults:policy.saved.defaults,wording_groups:policy.saved.wording_groups});
      assert.equal(source.revision,failed ? "source-1" : "source-2");
      assert.equal(source.pending,false);assert.equal(writes.length,1);
      retire(panel);
    });
  }

for(const policy of policies) {
  test(`${policy.kind}: initial user resolution binds before admission without losing the draft`, async () => {
    const {panel}=setup(policy,true), scope=currentPageScope(panel), writes=[];
    panel._guestMigrationReview=true;panel._guestStartingFresh=true;
    panel.hass={...panel.hass,user:{id:"first-resolved-user"}};
    panel._hass.callWS=async request => {writes.push(request);return structuredClone(policy.saved);};
    await scope.save();
    assert.equal(currentPageScope(panel),scope);
    assert.equal(scope.dirty(),false);assert.equal(writes.length,1);
    panel.hass={...panel.hass,user:undefined};
    panel.hass={...panel.hass,user:{id:"second-user"}};
    await assert.rejects(scope.save(),/owner changed/);
    assert.equal(writes.length,1,"a resolved initial owner cannot be rebound");
    retire(panel);
  });

  test(`${policy.kind}: initial identity cannot bind an already dispatched save to a new read`, async () => {
    const {panel}=setup(policy,true), source=currentPageScope(panel), gate=deferred(), writes=[];
    panel._hass.callWS=async request => {writes.push(request);return gate.promise;};
    const pending=source.save();await flushUntil(() => writes.length === 1);
    panel.hass={...panel.hass,user:{id:"first-resolved-user"}};
    panel._result=structuredClone(policy.target);initializePageDraft(panel);
    const target=currentPageScope(panel), draft=panel[policy.field];
    gate.resolve(structuredClone(policy.saved));await pending;
    assert.notEqual(target,source);assert.equal(panel[policy.field],draft);
    assert.deepEqual(panel._result,policy.target);assert.equal(target.revision,"target-1");
    assert.equal(writes.length,1);retire(panel);
  });

  test(`${policy.kind}: exact scope replacement retires old callbacks and save admission`, async () => {
    const {panel}=setup(policy), source=currentPageScope(panel), gate=deferred(), writes=[];
    panel._hass.callWS=async request => {writes.push(request);return gate.promise;};
    const pending=source.save();await flushUntil(() => writes.length === 1);
    panel._unsavedState.scopes.delete(policy.view);
    panel._result=structuredClone(policy.target);initializePageDraft(panel);
    const target=currentPageScope(panel), draft=panel[policy.field];
    gate.resolve(structuredClone(policy.saved));await pending;
    assert.equal(panel[policy.field],draft);assert.equal(target.revision,"target-1");
    assert.deepEqual(panel._result,policy.target);
    await assert.rejects(source.save(),/owner changed/);
    assert.equal(writes.length,1,"retired scope must not dispatch another request");
    retire(panel);
  });

  for(const detached of [false,true]) test(`${policy.kind}: current scope settles with detached=${detached}`, async () => {
    const {panel}=setup(policy), gate=deferred(), writes=[];
    const source=currentPageScope(panel);
    panel._hass.callWS=async request => {writes.push(request);return gate.promise;};
    const pending=source.save();await flushUntil(() => writes.length === 1);
    panel.isConnected=!detached;gate.resolve(structuredClone(policy.saved));await pending;
    assert.deepEqual(panel[policy.field],policy.saved.config || {defaults:policy.saved.defaults,wording_groups:policy.saved.wording_groups});
    assert.equal(source.dirty(),false);assert.equal(source.pending,false);assert.equal(source.revision,"source-2");
    assert.equal(writes.length,1);retire(panel);
  });
}

for(const change of ["user","session"]) for(const failed of [false,true]) {
  test(`Guest metrics use the same captured owner after ${change}, failed=${failed}`, async () => {
    const policy=policies[0], {panel}=setup(policy), gate=deferred(), warnings=[];
    panel._toast=(...args) => warnings.push(args);
    panel._hass.callWS=async request => request.action === "policy" ? gate.promise : structuredClone(policy.saved);
    const scope=currentPageScope(panel);await scope.save();scope.refreshSavedPolicy();
    const result=panel._result;
    transition(panel,change);
    if(failed) gate.reject(Error("private old-owner metrics"));
    else gate.resolve({policy:{readable_entity_count:999}});
    await scope.refreshPromise;
    assert.equal(panel._result,result);assert.deepEqual(warnings,[]);
    assert.equal(scope.refreshWarning,null);retire(panel);
  });
}
