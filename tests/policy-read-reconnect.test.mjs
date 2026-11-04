import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, retainPanelDrafts, restorePanelDrafts, flushUntil} from "./management-ownership-harness.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {capturePageRead, acceptPageRead} from "../custom_components/extended_openai_conversation_responses/frontend/page-save-acknowledgement.js";
import {captureConfigurationDraftOwner} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
import {deferred} from "./frontend-test-helpers.mjs";

const view="capabilities/guest-mode";
for(const transport of ["function","connection"]) for(const boundary of ["current","user","assistant","draft","failed retry"]) test(`failed owned policy recovery retries on ${transport} reconnect: ${boundary}`,async t => {
  const f=fixture(),source=f.create(),target=f.create(),gate=deferred();
  let writes=0,reads=0,healthy=false;
  const saved={config:{guest_function_policy:"on",guest_web_search:true},revision:"r1"};
  for(const panel of [source,target]) {
    panel._page="capabilities";panel._subsection="guest-mode";
    panel._result=panel === source ? {config:{guest_function_policy:"off",guest_web_search:false},revision:"r0"} : structuredClone(saved);
    initializePageDraft(panel);
  }
  const connection={connected:false};
  const closed=async () => {reads++;throw Error("Connection is closed");};
  const ready=async () => {reads++;if(!healthy) throw Error("Read still unavailable");return structuredClone(saved);};
  const stable=async request => connection.connected ? ready(request) : closed(request);
  target.hass={...target.hass,callWS:transport === "function" ? closed : stable,...(transport === "connection" ? {connection} : {})};
  target._loadSection=async () => {
    target._loadToken++;const read=capturePageRead(target);
    try {target._result=await target._call("guest_mode","get");target._error=null;acceptPageRead(target,target._result,read);initializePageDraft(target);}
    catch(error) {target._error=error.message;}
  };
  source._hass.callWS=async () => {writes++;await gate.promise;throw {code:3,message:"Reply lost"};};
  Object.assign(source._guestDraft,{guest_function_policy:"on",guest_web_search:true});
  const operation=currentPageScope(source).save();await flushUntil(() => writes === 1);
  source._guestDraft.guest_function_policy="off";source.isConnected=false;retainPanelDrafts(source);
  // Keep this test focused on read retry: a clean destination receives the
  // newer source reversion once its authoritative read becomes available.
  gate.resolve();await assert.rejects(operation,/unknown/);restorePanelDrafts(target);
  t.after(() => {for(const panel of [source,target]) {panel.isConnected=false;panel._unsavedState.scopes.clear();retire(panel);}});
  await flushUntil(() => target.toasts.some(message => message.includes("outcome is unknown")));
  assert.equal(reads,1);assert.equal(target._error,"Connection is closed");
  for(let i=0;i<3;i++) target.hass={...target.hass};
  await new Promise(resolve => setImmediate(resolve));assert.equal(reads,1,"ordinary hass updates do not retry a failed read");
  if(boundary === "assistant") target._agentId=f.agentB;
  if(boundary === "draft") target._eocDraftGeneration=(target._eocDraftGeneration||0)+1;
  if(boundary === "user") {
    // Retire the identity without mounting another account's presentation.
    target._hass={...target._hass,user:{id:"different-user"}};
  }
  healthy=boundary !== "failed retry";
  if(transport === "connection") connection.connected=true;
  target.hass={...target.hass,callWS:transport === "function" ? ready : stable};
  if(["user","assistant","draft"].includes(boundary)) {
    await new Promise(resolve => setImmediate(resolve));assert.equal(reads,1);assert.equal(writes,1);
    assert.equal(source._guestDraft.guest_function_policy,"off");return;
  }
  if(boundary === "failed retry") {
    await flushUntil(() => reads === 2 && target._error === "Read still unavailable");
    for(let i=0;i<3;i++) target.hass={...target.hass};
    await new Promise(resolve => setImmediate(resolve));assert.equal(reads,2,"one reconnect permits only one automatic read");
    healthy=true;target._error=null;target._loadToken++;restorePanelDrafts(target);
  }
  await flushUntil(() => target._guestDraft.guest_function_policy === "off");
  assert.equal(target._error,null);assert.equal(currentPageScope(target).revision,"r1");
  assert.equal(currentPageScope(target).dirty(),true);assert.equal(writes,1);
  assert.equal(captureConfigurationDraftOwner(target).userId,f.state.user);
});
