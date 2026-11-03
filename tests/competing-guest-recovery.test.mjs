import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, retainPanelDrafts, restorePanelDrafts, flushUntil} from "./management-ownership-harness.mjs";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {capturePageRead, acceptPageRead} from "../custom_components/extended_openai_conversation_responses/frontend/page-save-acknowledgement.js";
import {captureGuestInterval} from "../custom_components/extended_openai_conversation_responses/frontend/management-guest-feature.js";
import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
import {same} from "../custom_components/extended_openai_conversation_responses/frontend/unsaved-state.js";

const view="capabilities/guest-mode";
const copy=value => structuredClone(value);
TestEventTarget.prototype.append=function() {};
function dialogs(panel, inputs={}) {
  panel.reviews=[];
  panel.shadowRoot.querySelector=key => key === "dialog[open]" ? panel.reviews.find(dialog => dialog.open) || null : inputs[key] || null;
  panel.shadowRoot.append=dialog => panel.reviews.push(dialog);
}
function documentStub() {
  const previous=globalThis.document;
  globalThis.document={createElement(tagName) {
    const listeners=new Map();
    return {tagName,dataset:{},children:[],append(...nodes) {this.children.push(...nodes);},setAttribute() {},
      addEventListener(type,listener) {const list=listeners.get(type)||[];list.push(listener);listeners.set(type,list);},
      showModal() {this.open=true;},close() {this.open=false;for(const listener of listeners.get("close")||[]) listener();},remove() {}};
  }};
  return () => {globalThis.document=previous;};
}
function reviewValues(panel) {
  const dialog=panel.reviews.find(dialog => dialog.open);
  assert.ok(dialog,"both authored drafts must be available in an explicit review");
  return JSON.parse(dialog.children.find(child => child.tagName === "textarea").value);
}
function cleanup(panels, reset) {
  for(const panel of panels) {
    panel.isConnected=false;
    for(const scope of panel._unsavedState?.scopes.values()||[]) {scope.acknowledgedSave=null;scope.uncertainSubmission=null;scope.discard?.();}
    panel._unsavedState?.scopes.clear();
    retire(panel);
  }
  reset();
}

for(const editor of ["policy","interval"]) for(const destination of ["disjoint","conflicting"]) for(const reply of ["success","malformed","lost"]) for(const visits of [1,2]) for(const authoring of ["revert","aba","clear_restore"]) test(`competing Guest ${editor}: ${destination}, ${reply}, visits=${visits}, ${authoring}`,async t => {
  const f=fixture(),panels=[],commit=deferred(),response=deferred();
  const source=f.create();panels.push(source);source._page="capabilities";source._subsection="guest-mode";
  await routeAssetPromise(view,source);
  let saved={config:{guest_web_search:false,guest_function_policy:"off"},revision:"r0",status:{active_from:null,active_until:null,indefinite:false}},writes=0,committed=false;
  const setup=panel => {
    panel._page="capabilities";panel._subsection="guest-mode";panel._result=copy(saved);initializePageDraft(panel);
    const inputs={"#guest-start":{value:panel._dateTimeLocal(saved.status.active_from)},"#guest-end":{value:"",checkValidity:()=>true},"#guest-indefinite":{checked:saved.status.indefinite}};
    dialogs(panel,inputs);
    if(editor === "interval") captureGuestInterval(panel);
    panel._refreshGuestModeMutation=async () => {};
    panel._hass.callWS=async request => {
      writes++;await commit.promise;
      if(editor === "policy") saved={...saved,config:copy(request.config),revision:"r1"};
      else saved={...saved,status:{active_from:request.local_times?.active_from ? `${request.active_from}:00Z` : request.active_from,active_until:null,indefinite:true}};
      committed=true;await response.promise;
      if(reply === "lost") throw {code:3,message:"Connection lost"};
      return reply === "malformed" ? {} : editor === "policy" ? copy(saved) : {status:copy(saved.status)};
    };
    panel._loadSection=async () => {
      const read=capturePageRead(panel);panel._result=copy(saved);acceptPageRead(panel,panel._result,read);initializePageDraft(panel);
    };
    return inputs;
  };
  const inputs=setup(source),reset=documentStub();
  let operation;
  t.after(async () => {commit.resolve();response.resolve();await operation?.catch(() => {});cleanup(panels,reset);});
  if(editor === "policy") {
    Object.assign(source._guestDraft,{guest_web_search:true,guest_function_policy:"on"});operation=currentPageScope(source).save();
  } else {
    inputs["#guest-start"].value="2027-06-04T12:00";inputs["#guest-end"].value="2027-06-04T13:00";inputs["#guest-indefinite"].checked=true;
    captureGuestInterval(source);operation=source._updateGuestMode();
  }
  await flushUntil(() => writes === 1);
  if(editor === "policy") source._guestDraft.guest_function_policy="off";
  else {inputs[authoring === "clear_restore" ? "#guest-end" : "#guest-start"].value=authoring === "clear_restore" ? "" : "2027-06-04T13:00";captureGuestInterval(source);}
  commit.resolve();await flushUntil(() => committed);
  if(editor === "policy") {
    source._guestDraft.guest_function_policy="on";
    source._guestDraft.guest_function_policy=authoring === "clear_restore" ? "custom" : "off";
  } else {
    inputs["#guest-start"].value="2027-06-04T12:00";captureGuestInterval(source);
    inputs[authoring === "clear_restore" ? "#guest-end" : "#guest-start"].value=authoring === "clear_restore" ? "2027-06-04T17:00" : authoring === "revert" ? "" : "2027-06-04T13:00";
    captureGuestInterval(source);
  }
  const read=panel => editor === "policy" ? panel._guestDraft : panel._guestIntervalDraft.values;
  const original=copy(read(source));
  source.isConnected=false;retainPanelDrafts(source);
  let target=f.create();panels.push(target);const targetInputs=setup(target);restorePanelDrafts(target);
  if(editor === "policy") {
    if(destination === "disjoint") target._guestDraft.guest_web_search=false;
    else target._guestDraft.guest_function_policy=source._guestDraft.guest_function_policy === "off" ? "custom" : "off";
  } else {
    const field=(destination === "disjoint") !== (authoring === "clear_restore") ? "#guest-end" : "#guest-start";
    targetInputs[field].value="2027-06-04T16:00";captureGuestInterval(target);
  }
  const authoredDestination=copy(read(target));
  if(visits === 2) {target.isConnected=false;retainPanelDrafts(target);target=f.create();panels.push(target);setup(target);restorePanelDrafts(target);}
  response.resolve();await operation.catch(() => {});
  try {
    restorePanelDrafts(target);
    await flushUntil(() => target.reviews.some(dialog => dialog.open));
    const values=reviewValues(target);
    assert.ok(same(values.recovered,original) && same(values.current,authoredDestination)
      || same(values.current,original) && same(values.recovered,authoredDestination));
    assert.equal(writes,1,"review is read-only recovery");
    const dialog=target.reviews.find(dialog => dialog.open);
    dialog.children.find(child => child.textContent === "Keep draft for later").onclick();
    await new Promise(resolve => setImmediate(resolve));restorePanelDrafts(target);
    assert.equal(target.reviews.filter(dialog => dialog.open).length,0,"keeping both drafts must not reopen the same review immediately");
    target._loadToken++;restorePanelDrafts(target);
    await flushUntil(() => target.reviews.some(dialog => dialog.open));
    assert.equal(writes,1);
  } finally {commit.resolve();response.resolve();}
});

for(const change of ["discard","source edit","destination edit","baseline","visit","authentication return","different user"]) test(`competing policy review safely handles ${change}`,async t => {
  const f=fixture(),source=f.create(),target=f.create(),panels=[source,target];
  for(const panel of panels) {
    panel._page="capabilities";panel._subsection="guest-mode";
    panel._result={config:{guest_function_policy:"off",guest_web_search:false},revision:"r0"};initializePageDraft(panel);dialogs(panel);
  }
  source._guestDraft.guest_function_policy="custom";target._guestDraft.guest_web_search=true;
  const from=currentPageScope(source),to=currentPageScope(target),baseline=copy(to.baseline),destination=copy(to.read());
  const reset=documentStub();t.after(() => cleanup(panels,reset));source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
  const dialog=target.reviews.find(dialog => dialog.open);assert.ok(dialog);
  if(change === "source edit") source._guestDraft.guest_function_policy="on";
  if(change === "destination edit") target._guestDraft.guest_function_policy="on";
  if(change === "baseline") from.baseline.guest_web_search=true;
  if(change === "visit") target._loadToken++;
  if(change === "authentication return") target._eocAuthentication={userId:target.hass.user.id,generation:2};
  if(change === "different user") target._hass={...target._hass,user:{id:"another-user"}};
  dialog.children.find(child => child.textContent === "Discard recovered edits").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(from.dirty(),change !== "discard");
  assert.deepEqual(to.baseline,baseline);assert.equal(to.revision,"r0");
  if(change !== "destination edit") assert.deepEqual(to.read(),destination,"discarding the source cannot consume the destination");
  if(change === "different user" || change === "discard") assert.equal(target.reviews.filter(dialog => dialog.open).length,0);
  else assert.equal(target.reviews.filter(dialog => dialog.open).length,1,"a stale copy is replaced by a current owned review");
});

for(const kind of ["quiet-hours","request-rules"]) test(`competing ${kind} drafts use the same visible review`,async t => {
  const f=fixture(),source=f.create(),target=f.create(),panels=[source,target];
  for(const panel of panels) {
    panel._page="capabilities";panel._subsection=kind;
    panel._result=kind === "quiet-hours" ? {config:{enabled:false,start:"22:00",end:"07:00"},revision:"r0"}
      : {defaults:{fuzzy:false,word_forms:true},wording_groups:[],revision:"r0"};
    initializePageDraft(panel);dialogs(panel);
  }
  const from=currentPageScope(source),to=currentPageScope(target);
  if(kind === "quiet-hours") {from.read().start="23:00";to.read().enabled=true;}
  else {from.read().defaults.fuzzy=true;to.read().defaults.word_forms=false;}
  const original=copy(from.read()),destination=copy(to.read()),sourceBaseline=copy(from.baseline),targetBaseline=copy(to.baseline);
  const reset=documentStub();t.after(() => cleanup(panels,reset));source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
  assert.deepEqual(reviewValues(target),{recovered:original,current:destination});
  assert.deepEqual(from.baseline,sourceBaseline);assert.deepEqual(to.baseline,targetBaseline);
  assert.equal(from.revision,"r0");assert.equal(to.revision,"r0");
});

test("policy and interval reviews are sequenced and Discard retires only the reviewed scope",async t => {
  const f=fixture(),source=f.create(),target=f.create(),panels=[source,target];
  for(const panel of panels) {
    panel._page="capabilities";panel._subsection="guest-mode";
    panel._result={config:{guest_function_policy:"off",guest_web_search:false},revision:"r0",status:{active_from:null,active_until:null,indefinite:false}};
    initializePageDraft(panel);
    const inputs={"#guest-start":{value:""},"#guest-end":{value:""},"#guest-indefinite":{checked:false}};
    dialogs(panel,inputs);captureGuestInterval(panel);
    inputs[panel === source ? "#guest-start" : "#guest-end"].value="2027-06-04T16:00";
    inputs["#guest-indefinite"].checked=true;captureGuestInterval(panel);
  }
  source._guestDraft.guest_function_policy="custom";target._guestDraft.guest_web_search=true;
  const reset=documentStub();t.after(() => cleanup(panels,reset));source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
  let dialog=target.reviews.find(dialog => dialog.open);
  assert.equal(dialog.children[0].textContent,"Recovered Guest Mode interval");
  dialog.children.find(child => child.textContent === "Keep draft for later").onclick();
  await new Promise(resolve => setImmediate(resolve));
  dialog=target.reviews.find(dialog => dialog.open);assert.equal(dialog.children[0].textContent,"Recovered Guest Mode policy");
  dialog.children.find(child => child.textContent === "Discard recovered edits").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(currentPageScope(source).dirty(),false);
  assert.equal(source._unsavedState.scopes.get("guest-interval").dirty(),true);
  assert.equal(target._guestDraft.guest_web_search,true);
  assert.equal(target._guestIntervalDraft.values.end,"2027-06-04T16:00");
  assert.equal(target.reviews.filter(dialog => dialog.open).length,0);
  target._loadToken++;restorePanelDrafts(target);
  assert.equal(target.reviews.find(dialog => dialog.open).children[0].textContent,"Recovered Guest Mode interval");
  assert.equal(f.state.writes.length,0);
});

for(const reply of ["success","malformed","lost"]) for(const competing of [false,true]) test(`a fully reverted Guest interval remains recoverable after ${reply}, competing=${competing}`,async t => {
  const f=fixture(),source=f.create(),panels=[source],gate=deferred();
  source._page="capabilities";source._subsection="guest-mode";await routeAssetPromise(view,source);
  source._result={status:{active_from:null,active_until:null,indefinite:true}};
  const inputs={"#guest-start":{value:""},"#guest-end":{value:"",checkValidity:()=>true},"#guest-indefinite":{checked:true}};
  dialogs(source,inputs);captureGuestInterval(source);
  const reset=documentStub();let operation,writes=0;
  t.after(async () => {gate.resolve();await operation;cleanup(panels,reset);});
  source._refreshGuestModeMutation=async () => {};
  source._hass.callWS=async () => {writes++;await gate.promise;if(reply === "lost") throw {code:3,message:"Connection lost"};return reply === "malformed" ? {} : {status:{active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true}};};
  inputs["#guest-start"].value="2027-06-04T12:00";captureGuestInterval(source);operation=source._updateGuestMode();
  await flushUntil(() => writes === 1);inputs["#guest-start"].value="";captureGuestInterval(source);
  source.isConnected=false;retainPanelDrafts(source);
  const target=f.create();panels.push(target);target._page="capabilities";target._subsection="guest-mode";
  target._result={status:{active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true}};
  const targetInputs={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:competing ? "2027-06-04T16:00" : ""},"#guest-indefinite":{checked:true}};
  dialogs(target,targetInputs);captureGuestInterval(target);
  restorePanelDrafts(target);gate.resolve();await operation;restorePanelDrafts(target);
  if(competing) {
    await flushUntil(() => target.reviews.some(dialog => dialog.open));
    assert.equal(reviewValues(target).recovered.start,"");assert.equal(reviewValues(target).current.end,"2027-06-04T16:00");
  } else {
    assert.equal(target._guestIntervalDraft.values.start,"");
    assert.equal(target._unsavedState.scopes.get("guest-interval").dirty(),true);
    targetInputs["#guest-start"].value="";captureGuestInterval(target);
    assert.equal(target._guestIntervalDraft.values.start,"");
    assert.equal(target._unsavedState.scopes.get("guest-interval").dirty(),true,"render/capture must not erase the transferred uncertainty");
    assert.equal(target.reviews.length,0);
  }
  assert.equal(writes,1);
});
