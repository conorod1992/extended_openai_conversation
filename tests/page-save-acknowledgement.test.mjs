import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {capturePageRead, acceptPageRead, hasPageSaveAcknowledgement, hasPendingPageSaveRead} from "../custom_components/extended_openai_conversation_responses/frontend/page-save-acknowledgement.js";

const policies = [
  {view:"capabilities/guest-mode",result:{config:{guest_web_search:false},revision:"guest-1",legacy_policy:false},edit:panel => {panel._guestDraft.guest_web_search=true;}},
  {view:"capabilities/quiet-hours",result:{config:{enabled:false,start:"22:00",end:"07:00",wake_sound:"off"}},edit:panel => {panel._quietHoursDraft.enabled=true;}},
  {view:"capabilities/request-rules",result:{defaults:{fuzzy:false},wording_groups:[],revision:"rules-1"},edit:panel => {panel._rulesSettingsDraft.defaults.fuzzy=true;}},
];

function policyPanel(create, policy) {
  const panel=create();[panel._page,panel._subsection]=policy.view.split("/");
  panel._result=structuredClone(policy.result);panel._busy=false;initializePageDraft(panel);
  panel._hass.callWS=async request => request.action === "settings"
    ? {defaults:request.defaults,wording_groups:request.wording_groups,revision:"rules-2"}
    : {config:request.config,revision:policy.view.endsWith("quiet-hours") ? undefined : "guest-2"};
  return panel;
}

for (const policy of policies) for (const failed of [false, true]) {
  test(`${policy.view}: retired held refresh cannot block a current authentication attempt, failure=${failed}`, async t => {
    const {create} = fixture(), source = policyPanel(create, policy), target = policyPanel(create, policy);
    policy.edit(source); source.isConnected = false;
    const scope = currentPageScope(source); await scope.save();
    const acknowledgement = scope.acknowledgedSave, gates = [deferred(), deferred()], reads = [];
    target.toasts.length = 0;
    target._loadSection = async () => {
      const index = reads.length;
      reads.push(capturePageRead(target));
      await gates[index].promise;
    };
    t.after(() => {gates.forEach(gate => gate.resolve()); scope.acknowledgedSave = null; retire(source, target);});
    assert.deepEqual(acknowledgement(target, () => {}), {accepted:false, refreshing:true});
    await new Promise(resolve => setImmediate(resolve));
    const user = target.hass.user;
    target.hass = {...target.hass, user:{id:"another-user"}};
    target.hass = {...target.hass, user};
    assert.deepEqual(acknowledgement(target, () => {}), {accepted:false, refreshing:true});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(reads.length, 2, "an obsolete held request must not retain the refresh slot");
    if (failed) gates[0].reject(Error("obsolete refresh failed")); else gates[0].resolve();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(hasPendingPageSaveRead(target), true, "the authoritative read obligation survives attempt retirement");
    assert.deepEqual(target.toasts, []);
    assert.deepEqual(acknowledgement(target, () => {}), {accepted:false, refreshing:true});
    assert.equal(reads.length, 2);
    target._result = structuredClone(scope.result);
    initializePageDraft(target);
    assert.equal(acceptPageRead(target, target._result, reads[1]), true);
    gates[1].resolve(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(hasPendingPageSaveRead(target), false);
    assert.deepEqual(acknowledgement(target, () => {}), {accepted:true, refreshing:false});
  });
}

for(const revisioned of [false,true]) test(`Quiet Hours observed conflict respects backend revision support: ${revisioned}`, async () => {
  const {create}=fixture(), policy=policies.find(policy => policy.view.endsWith("quiet-hours"));
  const configured={...policy,result:{...structuredClone(policy.result),...(revisioned ? {revision:"old-policy"} : {})}};
  const panel=policyPanel(create,configured), writes=[];
  policy.edit(panel);
  const scope=currentPageScope(panel), original=structuredClone(scope.baseline);
  panel._result={config:{...structuredClone(original),end:"04:02"},...(revisioned ? {revision:"new-policy"} : {})};
  initializePageDraft(panel);
  panel._hass.callWS=async request => {
    writes.push(structuredClone(request));
    if(revisioned && request.revision !== "new-policy") throw new Error("Quiet Hours configuration changed; reload before saving");
    return {config:request.config,...(revisioned ? {revision:"new-policy"} : {})};
  };
  await assert.rejects(scope.save(),revisioned ? /reload before saving/ : /saved Quiet Hours policy changed/);
  assert.equal(writes.length,revisioned ? 1 : 0);
  if(revisioned) assert.equal(writes[0].revision,"old-policy");
  assert.equal(panel._quietHoursDraft.enabled,true);
  assert.deepEqual(scope.baseline,original);
  scope.discard();
  assert.equal(panel._quietHoursDraft.end,"04:02");
  assert.equal(scope.revision,revisioned ? "new-policy" : undefined);
  policy.edit(panel);await scope.save();
  assert.equal(writes.length,revisioned ? 2 : 1);
  assert.equal(writes.at(-1).config.end,"04:02");
  assert.equal(scope.dirty(),false);retire(panel);
});

for(const policy of policies) {
  test(`${policy.view}: clean detached ACK survives metadata scope refresh until consumed`, async () => {
    const {create}=fixture(), panel=policyPanel(create,policy);policy.edit(panel);panel.isConnected=false;
    const scope=currentPageScope(panel);await scope.save();
    const baseline=structuredClone(scope.baseline),revision=scope.revision;
    assert.equal(scope.dirty(),false);assert.equal(hasPageSaveAcknowledgement(panel),true);
    panel._result={...panel._result,metadata:"new metadata"};initializePageDraft(panel);
    assert.equal(currentPageScope(panel),scope);assert.equal(scope.acknowledgedSave.owns(),true);
    assert.deepEqual(scope.baseline,baseline);assert.equal(scope.revision,revision);
    scope.acknowledgedSave=null;
    panel._result={...panel._result,metadata:"after consumption"};initializePageDraft(panel);
    assert.notEqual(currentPageScope(panel),scope,"a consumed clean scope resumes normal refresh behavior");
    retire(panel);
  });

  test(`${policy.view}: ordinary connected save needs no ACK handoff`, async () => {
    const {create}=fixture(), panel=policyPanel(create,policy);policy.edit(panel);
    const scope=currentPageScope(panel);await scope.save();
    assert.equal(scope.dirty(),false);assert.equal(hasPageSaveAcknowledgement(panel),false);
    panel._result={...panel._result,metadata:"connected refresh"};initializePageDraft(panel);
    assert.notEqual(currentPageScope(panel),scope);
    retire(panel);
  });

  test(`${policy.view}: resolved load is insufficient without an accepted current read`, async () => {
    const {create}=fixture(), source=policyPanel(create,policy),target=policyPanel(create,policy);
    policy.edit(source);source.isConnected=false;
    const scope=currentPageScope(source);await scope.save();const acknowledgement=scope.acknowledgedSave;
    assert.equal(acceptPageRead(target,target._result,capturePageRead(target)),true);
    let loads=0;target._loadSection=async () => {loads++;};
    assert.deepEqual(acknowledgement(target,()=>{}),{accepted:false,refreshing:true});
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(acknowledgement(target,()=>{}),{accepted:false,refreshing:false});
    assert.equal(loads,1);assert.equal(hasPageSaveAcknowledgement(source),true);
    assert.equal(hasPendingPageSaveRead(target),true);
    policy.edit(target);
    await assert.rejects(currentPageScope(target).save(),/still being refreshed/);
    const page=target._page,section=target._subsection,user=target.hass.user,agent=target._agentId;
    target._page="guide";target._subsection="learn";
    assert.equal(hasPendingPageSaveRead(target),false,"another route is not blocked by this policy read");
    target._page=page;target._subsection=section;
    target.hass={...target.hass,user:{id:"different-owner"}};
    assert.equal(hasPendingPageSaveRead(target),false);
    target.hass={...target.hass,user};target._agentId=target._data.agents[1].subentry_id;
    assert.equal(hasPendingPageSaveRead(target),false);
    assert.equal(acceptPageRead(target,target._result,capturePageRead(target)),true);
    target._agentId=agent;
    assert.equal(hasPendingPageSaveRead(target),true,"another assistant's read cannot certify the original policy");
    target._result=structuredClone(scope.result);initializePageDraft(target);
    assert.equal(acceptPageRead(target,target._result,capturePageRead(target)),true);
    assert.equal(hasPendingPageSaveRead(target),false);
    assert.deepEqual(acknowledgement(target,()=>{}),{accepted:true,refreshing:false});
    const savedUser=target.hass.user;target.hass={...target.hass,user:{id:"different-owner"}};
    assert.deepEqual(acknowledgement(target,()=>{}),{accepted:false,refreshing:false});
    target.hass={...target.hass,user:savedUser};scope.acknowledgedSave=null;retire(source,target);
  });
}
