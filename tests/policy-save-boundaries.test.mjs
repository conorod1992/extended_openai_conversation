import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, saveConfiguration} from "./management-ownership-harness.mjs";
import {initializePageDraft, currentPageScope, savePageChanges} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";

const policies = [
  {kind:"quiet",view:"capabilities/quiet-hours",initial:{config:{enabled:false},revision:"r1"},edit:p=>{p._quietHoursDraft.enabled=true;}},
  {kind:"rules",view:"capabilities/request-rules",initial:{defaults:{fuzzy:false},wording_groups:[],revision:"r1"},edit:p=>{p._rulesSettingsDraft.defaults.fuzzy=true;}},
  {kind:"guest",view:"capabilities/guest-mode",initial:{config:{guest_web_search:false},revision:"r1",legacy_policy:false},edit:p=>{p._guestDraft.guest_web_search=true;}},
];

function setup(policy) {
  const {create}=fixture(),panel=create();[panel._page,panel._subsection]=policy.view.split("/");
  panel._result=structuredClone(policy.initial);panel._busy=false;
  initializePageDraft(panel);policy.edit(panel);
  return panel;
}

const unrelated = () => ({toJSON() {throw Error("unrelated configuration traversed");}});

for(const policy of policies.slice(0,2)) for(const failed of [false,true]) {
  test(`${policy.kind}: owner-only page save avoids unrelated snapshots, failure=${failed}`, async () => {
    const panel=setup(policy),requests=[],messages=[];
    panel._configData.config=unrelated();panel._draft=unrelated();
    panel._toast=(...args)=>messages.push(args);
    panel._hass.callWS=async request => {
      requests.push(structuredClone(request));
      if(failed)throw Error("controlled transport failure");
      return policy.kind === "rules" ? {defaults:request.defaults,wording_groups:request.wording_groups,revision:"r2"}
        : {config:request.config,revision:"r2"};
    };
    const scope=currentPageScope(panel),baseline=structuredClone(scope.baseline);
    assert.equal(await savePageChanges(panel),!failed);
    assert.equal(requests.length,1);assert.equal(scope.pending,false);
    assert.equal(scope.dirty(),failed);
    if(failed) {
      assert.deepEqual(scope.baseline,baseline);
      assert.match(messages[0][0],/controlled transport failure/);
    }
    retire(panel);
  });
}

test("Guest field acknowledgement still requires its full configuration submission", async () => {
  const panel=setup(policies[2]);let requests=0;
  panel._configData.config=unrelated();panel._draft=unrelated();
  panel._hass.callWS=async ()=>{requests++;throw Error("must capture the field baseline before dispatch");};
  const scope=currentPageScope(panel),baseline=structuredClone(scope.baseline);
  await assert.rejects(scope.save(),/unrelated configuration traversed/);
  assert.equal(requests,0);assert.equal(scope.pending,false);
  assert.deepEqual(scope.baseline,baseline);assert.equal(scope.dirty(),true);retire(panel);
});

for(const policy of policies) test(`${policy.kind}: the shared draft scope adopts the successful baseline once`, async () => {
  const panel=setup(policy),scope=currentPageScope(panel);let assignments=0,baseline=scope.baseline;
  Object.defineProperty(scope,"baseline",{get:()=>baseline,set:value=>{assignments++;baseline=value;}});
  panel._hass.callWS=async request=>policy.kind === "rules"
    ? {defaults:request.defaults,wording_groups:request.wording_groups,revision:"r2"}
    : {config:request.config,revision:"r2"};
  await scope.save();
  assert.equal(assignments,1);assert.equal(scope.dirty(),false);assert.equal(scope.revision,"r2");retire(panel);
});

test("a required post-save read captures ownership without cloning the draft", async () => {
  const {create}=fixture(),panel=create();let reads=0;
  panel._configData.config=unrelated();panel._draft=unrelated();
  panel._eocAcknowledgedConfiguration={};panel._configDataStale=true;
  panel._loadConfigDraft=async ()=>{reads++;panel._configDataStale=false;};
  await saveConfiguration(panel,null);
  assert.equal(reads,1);assert.equal(panel._configDataStale,false);retire(panel);
});
