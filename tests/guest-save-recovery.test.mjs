import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, retainPanelDrafts, restorePanelDrafts, flushUntil} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {capturePageRead, acceptPageRead} from "../custom_components/extended_openai_conversation_responses/frontend/page-save-acknowledgement.js";
import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
import {captureGuestInterval, acknowledgeGuestInterval} from "../custom_components/extended_openai_conversation_responses/frontend/management-guest-feature.js";

const policies = [
  {view:"capabilities/guest-mode", result:{config:{guest_web_search:false},revision:"r0"}, field:"_guestDraft", key:"guest_web_search"},
  {view:"capabilities/quiet-hours", result:{config:{enabled:false,start:"22:00",end:"07:00"},revision:"r0"}, field:"_quietHoursDraft", key:"enabled"},
  {view:"capabilities/request-rules", result:{defaults:{fuzzy:false},wording_groups:[],revision:"r0"}, field:"_rulesSettingsDraft", key:"fuzzy"},
];
const draft = (panel, policy) => policy.key === "fuzzy" ? panel[policy.field].defaults : panel[policy.field];

for (const policy of policies) for (const reply of ["malformed", "lost", "success", "rejected"]) for(const detached of ["pending","settled"]) test(`${policy.view}: newer reversion survives ${reply} save, detached while ${detached}`, async () => {
  const f=fixture(), source=f.create(), target=f.create(), gate=deferred();
  let saved=structuredClone(policy.result), writes=0;
  const setup=panel => {
    [panel._page,panel._subsection]=policy.view.split("/"); panel._result=structuredClone(saved); initializePageDraft(panel);
    panel._hass.callWS=async request => {
      if (!["save_policy","update","settings"].includes(request.action)) throw Error(`Unexpected ${request.action}`);
      writes++; assert.equal(request.revision,saved.revision);
      if (reply !== "rejected") saved=policy.key === "fuzzy" ? {defaults:request.defaults,wording_groups:request.wording_groups,revision:"r1"} : {config:request.config,revision:"r1"};
      await gate.promise;
      if (reply === "lost" && writes === 1) throw {code:3,message:"Connection lost"};
      if (reply === "rejected") throw {code:"invalid_request",message:"Policy rejected"};
      return reply === "malformed" && writes === 1 ? {} : structuredClone(saved);
    };
    panel._loadSection=async () => {
      const read=capturePageRead(panel); panel._result=structuredClone(saved);
      acceptPageRead(panel,panel._result,read); initializePageDraft(panel);
    };
  };
  setup(source); setup(target);
  draft(source,policy)[policy.key]=true;
  const scope=currentPageScope(source), operation=scope.save();
  await flushUntil(() => writes === 1);
  draft(source,policy)[policy.key]=false;
  if(detached === "settled") {gate.resolve();await operation.catch(() => {});}
  source.isConnected=false; retainPanelDrafts(source);
  await target._loadSection(); restorePanelDrafts(target);
  if(detached === "pending") {gate.resolve();await operation.catch(() => {});}
  try {
    restorePanelDrafts(target);
    await flushUntil(() => draft(target,policy)[policy.key] === false && !currentPageScope(target).pending);
    assert.equal(draft(target,policy)[policy.key],false);
    assert.equal(currentPageScope(target).dirty(),reply !== "rejected");
    assert.equal(currentPageScope(target).revision,reply === "rejected" ? "r0" : "r1");
    assert.equal(writes,1,"recovery reads never repeat the mutation");
    if (reply !== "rejected") {
      await target._loadSection();
      assert.equal(draft(target,policy)[policy.key],false,"another read must not erase the recovered reversion");
      await currentPageScope(target).save();
      assert.equal(writes,2); assert.equal(draft(target,policy)[policy.key],false); assert.equal(currentPageScope(target).dirty(),false);
    }
  } finally {
    for (const panel of [source,target]) for (const scope of panel._unsavedState.scopes.values()) {scope.acknowledgedSave=null;scope.uncertainSubmission=null;scope.discard?.();}
    retire(source,target);
  }
});

for (const operation of ["update", "disable"]) for (const reply of ["malformed", "lost"]) for(const retired of [false,true]) test(`Guest ${operation}: ${reply} failure respects authentication lifetime, retired=${retired}`, async () => {
  const f=fixture(), panel=f.create(), gate=deferred();
  panel._page="capabilities"; panel._subsection="guest-mode";
  await routeAssetPromise("capabilities/guest-mode",panel);
  panel._result={status:{active_from:null,active_until:null,indefinite:true}};
  const controls={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:"2027-06-04T13:00",checkValidity:()=>true},"#guest-indefinite":{checked:true}};
  panel.shadowRoot.querySelector=key => controls[key] || null;
  let writes=0;panel._hass.callWS=async () => {writes++;await gate.promise;if(reply === "lost") throw {code:3,message:"Connection lost"};return {};};
  const pending=operation === "update" ? panel._updateGuestMode() : panel._disableGuestMode();
  await flushUntil(() => writes === 1);
  if(retired) {const user=panel.hass.user; panel.hass={...panel.hass,user:{id:"other-user"}};panel.hass={...panel.hass,user};}
  gate.resolve();await pending;
  assert.equal(panel.toasts.length,retired ? 0 : 1);
  if(operation === "update") assert.equal(panel._guestIntervalDraft.values.start,controls["#guest-start"].value,"retired presentation does not retire the authored interval");
  assert.equal(writes,1);retire(panel);
});

for(const policy of policies) for(const committed of [false,true]) for(const discard of [false,true]) test(`${policy.view}: connected uncertain save reconciles a fresh read; committed=${committed}, discard=${discard}`, async () => {
  const f=fixture(),panel=f.create(),gate=deferred();
  [panel._page,panel._subsection]=policy.view.split("/");panel._result=structuredClone(policy.result);initializePageDraft(panel);
  let saved=structuredClone(policy.result),writes=0;
  panel._hass.callWS=async request => {
    writes++;
    if(committed) saved=policy.key === "fuzzy" ? {defaults:request.defaults,wording_groups:request.wording_groups,revision:"r1"} : {config:request.config,revision:"r1"};
    await gate.promise;throw {code:3,message:"Connection lost"};
  };
  const scope=currentPageScope(panel);draft(panel,policy)[policy.key]=true;
  const operation=scope.save();await flushUntil(() => writes === 1);draft(panel,policy)[policy.key]=false;
  gate.resolve();await assert.rejects(operation,/unknown/);
  try {
    assert.equal(scope.dirty(),true,"a newer reversion is authored even against the old baseline");
    if(discard) {scope.discard();assert.equal(scope.dirty(),false);}
    await assert.rejects(scope.save(),/still being refreshed/);
    assert.equal(writes,1,"no retry may replace unresolved save evidence");
    const read=capturePageRead(panel);panel._result=structuredClone(saved);
    assert.equal(acceptPageRead(panel,panel._result,read),true);initializePageDraft(panel);
    assert.equal(draft(panel,policy)[policy.key],discard && committed);
    assert.equal(currentPageScope(panel).dirty(),committed && !discard);
    assert.equal(currentPageScope(panel).revision,committed ? "r1" : "r0");
  } finally {scope.uncertainSubmission=null;scope.discard();retire(panel);}
});

for(const change of ["navigation","authentication"]) test(`uncertain Guest policy recovery survives failed reads and ${change} retry`, async () => {
  const f=fixture(),source=f.create(),target=f.create(),gate=deferred(),policy=policies[0];
  for(const panel of [source,target]) {[panel._page,panel._subsection]=policy.view.split("/");panel._result=structuredClone(policy.result);initializePageDraft(panel);}
  source._hass.callWS=async () => {await gate.promise;return {};};
  draft(source,policy).guest_web_search=true;const operation=currentPageScope(source).save();
  draft(source,policy).guest_web_search=false;source.isConnected=false;retainPanelDrafts(source);
  let fail=true,reads=0;
  target._loadSection=async () => {
    reads++;if(fail) throw Error("Read unavailable");
    const read=capturePageRead(target);target._result={config:{guest_web_search:true},revision:"r1"};
    acceptPageRead(target,target._result,read);initializePageDraft(target);
  };
  gate.resolve();await assert.rejects(operation,/unknown/);restorePanelDrafts(target);
  try {
    await flushUntil(() => target.toasts.some(message => message.includes("outcome is unknown")));
    assert.equal(draft(source,policy).guest_web_search,false);
    restorePanelDrafts(target);assert.equal(reads,1,"a failed visit must not spin");
    fail=false;
    if(change === "navigation") target._loadToken++;
    else {const user=target.hass.user;target.hass={...target.hass,user:{id:"another"}};target.hass={...target.hass,user};}
    restorePanelDrafts(target);
    await flushUntil(() => currentPageScope(target).revision === "r1");
    assert.equal(draft(target,policy).guest_web_search,false);assert.equal(currentPageScope(target).dirty(),true);
  } finally {for(const panel of [source,target]) {const scope=currentPageScope(panel);scope.acknowledgedSave=null;scope.uncertainSubmission=null;scope.discard();}retire(source,target);}
});

for (const newer of [false,true]) test(`Guest indefinite ACK preserves inactive end ownership: newer=${newer}`, () => {
  const f=fixture(),source=f.create(),target=f.create();
  for(const panel of [source,target]) {panel._page="capabilities";panel._subsection="guest-mode";panel._result={status:{active_from:null,active_until:null,indefinite:true}};}
  const inputs={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:"2027-06-04T13:00"},"#guest-indefinite":{checked:true}};
  source.shadowRoot.querySelector=key => inputs[key] || null;
  const submitted=captureGuestInterval(source);
  if(newer) {inputs["#guest-end"].value="2027-06-04T14:00";captureGuestInterval(source);}
  acknowledgeGuestInterval(source,submitted,{active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true});
  assert.equal(source._unsavedState.scopes.get("guest-interval").dirty(),newer);
  if(newer) {
    source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
    assert.equal(target._guestIntervalDraft.values.end,"2027-06-04T14:00");
  }
  source._unsavedState.scopes.get("guest-interval").discard();target._unsavedState?.scopes.get("guest-interval")?.discard();retire(source,target);
});
