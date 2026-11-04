import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, retainPanelDrafts, restorePanelDrafts, flushUntil, edit, saveConfiguration} from "./management-ownership-harness.mjs";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";
import {initializePageDraft, currentPageScope} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";
import {capturePageRead, acceptPageRead} from "../custom_components/extended_openai_conversation_responses/frontend/page-save-acknowledgement.js";
import {captureGuestInterval} from "../custom_components/extended_openai_conversation_responses/frontend/management-guest-feature.js";
import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
import {same} from "../custom_components/extended_openai_conversation_responses/frontend/unsaved-state.js";
import {writeSectionCache} from "../custom_components/extended_openai_conversation_responses/frontend/management-cache.js";
import {watchTimedFeatureStatus, stopLiveStatus} from "../custom_components/extended_openai_conversation_responses/frontend/management-live-status.js";

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

for(const editor of ["configuration","policy","interval"]) test(`a kept ${editor} copy cannot replace a later saved destination`,async t => {
  const f=fixture(),source=f.create(),target=f.create(),panels=[source,target],reset=documentStub();
  t.after(() => cleanup(panels,reset));
  const saved={config:{guest_function_policy:"on",guest_web_search:true},revision:"r1",status:{active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true}};
  const setup=panel => {
    dialogs(panel);
    if(editor === "configuration") return;
    panel._page="capabilities";panel._subsection="guest-mode";panel._result=copy(saved);initializePageDraft(panel);
    if(editor === "interval") {
      const controls={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:""},"#guest-indefinite":{checked:true}};
      dialogs(panel,controls);captureGuestInterval(panel);
    }
  };
  setup(source);setup(target);
  if(editor === "configuration") {edit(source,"max_tokens",425);edit(target,"max_tokens",477);}
  else if(editor === "policy") {source._guestDraft.guest_function_policy="off";target._guestDraft.guest_function_policy="custom";}
  else {source._guestIntervalDraft.values.end="2027-06-04T17:00";target._guestIntervalDraft.values.end="2027-06-04T16:00";}
  source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
  const review=target.reviews.find(dialog => dialog.open);assert.ok(review);
  review.children.find(child => child.textContent === "Keep draft for later").onclick();
  if(editor === "configuration") {
    target._draftTitle="New saved title";target._syncConfigDirty();await saveConfiguration(target,null);
    assert.equal(f.state.snapshot(f.agentA).config.max_tokens,477);assert.equal(target._configDirty,false);
  } else if(editor === "policy") {
    target._hass.callWS=async request => {Object.assign(saved,{config:copy(request.config),revision:"r2"});return copy(saved);};
    await currentPageScope(target).save();assert.equal(currentPageScope(target).dirty(),false);
  } else {
    // Saving an indefinite interval retains its inactive end locally. A fresh
    // destination has no such input, and must still expose the kept source.
    target._guestIntervalDraft.baseline=copy(target._guestIntervalDraft.values);
  }
  target.isConnected=false;retainPanelDrafts(target);
  const reopened=f.create();panels.push(reopened);setup(reopened);restorePanelDrafts(reopened);
  assert.ok(reopened.reviews.some(dialog => dialog.open),"a kept copy requires review even after the destination became clean");
  if(editor === "configuration") {assert.equal(reopened._draft.max_tokens,477);assert.equal(reopened._draftTitle,"New saved title");}
  else if(editor === "policy") assert.equal(reopened._guestDraft.guest_function_policy,"custom");
  else assert.equal(reopened._guestIntervalDraft.values.end,"");
});

test("keeping a configuration conflict exposes an independently retained Guest policy",async t => {
  const f=fixture(),source=f.create(),target=f.create(),panels=[source,target],reset=documentStub();
  t.after(() => cleanup(panels,reset));
  for(const panel of panels) {
    panel._page="capabilities";panel._subsection="guest-mode";
    panel._result={config:{guest_function_policy:"off",guest_web_search:false},revision:"r0"};initializePageDraft(panel);dialogs(panel);
  }
  edit(source,"max_tokens",425);edit(target,"max_tokens",477);
  source._guestDraft.guest_function_policy="on";target._guestDraft.guest_web_search=true;
  source.isConnected=false;retainPanelDrafts(source);restorePanelDrafts(target);
  const configuration=target.reviews.find(dialog => dialog.open);assert.ok(configuration);
  assert.equal(configuration.children[0].textContent,"Recovered unsaved configuration");
  configuration.children.find(child => child.textContent === "Keep draft for later").onclick();
  await flushUntil(() => target.reviews.some(dialog => dialog.open));
  assert.deepEqual(reviewValues(target),{recovered:{guest_function_policy:"on",guest_web_search:false},current:{guest_function_policy:"off",guest_web_search:true}});
  assert.equal(source._configDirty,true);assert.equal(target._draft.max_tokens,477);
});

test("multiple kept configuration conflicts are reviewed once each in a visit",async t => {
  const f=fixture(),first=f.create(),second=f.create(),target=f.create(),panels=[first,second,target],reset=documentStub();
  t.after(() => cleanup(panels,reset));
  for(const [index,panel] of panels.entries()) {edit(panel,"max_tokens",425+index*52);dialogs(panel);}
  for(const panel of [first,second]) {panel.isConnected=false;retainPanelDrafts(panel);}
  restorePanelDrafts(target);
  for(let index=0;index<2;index++) {
    const dialog=target.reviews.find(value => value.open);assert.ok(dialog);
    assert.equal(JSON.parse(dialog.children.find(value => value.tagName === "textarea").value).config.max_tokens,425+index*52);
    dialog.children.find(value => value.textContent === "Keep draft for later").onclick();
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.equal(target.reviews.filter(value => value.open).length,0,"keeping the second copy must not reopen the first");
  assert.equal(target.reviews.length,2);assert.equal(first._configDirty,true);assert.equal(second._configDirty,true);
});

for(const transition of [false,true]) for(const reply of ["malformed","lost"]) test(`a recovered Guest policy retains a usable save baseline: ${reply}, authentication return=${transition}`,async t => {
  const f=fixture(),source=f.create(),gate=deferred(),panels=[source];
  source._page="capabilities";source._subsection="guest-mode";
  let saved={config:{guest_function_policy:"off",guest_web_search:false},revision:"r0"},writes=0;
  source._result=copy(saved);initializePageDraft(source);dialogs(source);
  const reset=documentStub();t.after(() => cleanup(panels,reset));
  source._hass.callWS=async request => {writes++;saved={config:copy(request.config),revision:`r${writes}`};await gate.promise;if(reply === "lost") throw {code:3,message:"Connection lost"};return {};};
  Object.assign(source._guestDraft,{guest_function_policy:"on",guest_web_search:true});
  const operation=currentPageScope(source).save();await flushUntil(() => writes === 1);
  source._guestDraft.guest_function_policy="off";source._guestDraft.guest_function_policy="on";source._guestDraft.guest_function_policy="off";
  if(transition) source._eocAuthentication={userId:source.hass.user.id,generation:2};
  source.isConnected=false;retainPanelDrafts(source);
  const target=f.create();panels.push(target);target._page="capabilities";target._subsection="guest-mode";target._result=copy(saved);initializePageDraft(target);dialogs(target);
  target._loadSection=async () => {const read=capturePageRead(target);target._result=copy(saved);acceptPageRead(target,target._result,read);initializePageDraft(target);};
  gate.resolve();await operation.catch(() => {});restorePanelDrafts(target);
  await flushUntil(() => target._guestDraft.guest_function_policy === "off");
  target._guestDraft.guest_web_search=false;
  const scope=currentPageScope(target);assert.equal(scope.dirty(),true,"off/false differs from the committed on/true policy");
  assert.deepEqual(scope.baseline,saved.config);assert.equal(scope.revision,"r1");
  target._hass.callWS=async request => {writes++;assert.equal(request.revision,"r1");saved={config:copy(request.config),revision:"r2"};return copy(saved);};
  await scope.save();assert.deepEqual(saved.config,{guest_function_policy:"off",guest_web_search:false});assert.equal(writes,2);
});

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

for(const visits of [1,2]) for(const reply of ["success","malformed","lost"]) test(`an interval destination's clear/restore intent remains visible: visits=${visits}, ${reply}`,async t => {
  const f=fixture(),source=f.create(),panels=[source],gate=deferred();
  source._page="capabilities";source._subsection="guest-mode";await routeAssetPromise(view,source);
  const status={active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true};
  source._result={status:{active_from:null,active_until:null,indefinite:false}};
  const inputs={"#guest-start":{value:""},"#guest-end":{value:"",checkValidity:()=>true},"#guest-indefinite":{checked:false}};
  dialogs(source,inputs);captureGuestInterval(source);const reset=documentStub();let writes=0,operation;
  t.after(async () => {gate.resolve();await operation;cleanup(panels,reset);});
  source._refreshGuestModeMutation=async () => {};
  source._hass.callWS=async () => {writes++;await gate.promise;if(reply === "lost") throw {code:3,message:"Connection lost"};return reply === "malformed" ? {} : {status};};
  Object.assign(inputs["#guest-start"],{value:"2027-06-04T12:00"});inputs["#guest-end"].value="2027-06-04T13:00";inputs["#guest-indefinite"].checked=true;
  captureGuestInterval(source);operation=source._updateGuestMode();await flushUntil(() => writes === 1);
  inputs["#guest-end"].value="";captureGuestInterval(source);inputs["#guest-end"].value="2027-06-04T17:00";captureGuestInterval(source);
  source.isConnected=false;retainPanelDrafts(source);
  const createTarget=() => {
    const target=f.create();panels.push(target);target._page="capabilities";target._subsection="guest-mode";target._result={status};
    const controls={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:""},"#guest-indefinite":{checked:true}};
    dialogs(target,controls);captureGuestInterval(target);restorePanelDrafts(target);return {target,controls};
  };
  let {target,controls}=createTarget();
  controls["#guest-indefinite"].checked=false;captureGuestInterval(target,"guest-indefinite");
  captureGuestInterval(target,"guest-end");controls["#guest-indefinite"].checked=true;captureGuestInterval(target,"guest-indefinite");
  assert.equal(target._unsavedState.scopes.get("guest-interval").dirty(),false,"the interval still matches its saved baseline");
  if(visits === 2) {target.isConnected=false;retainPanelDrafts(target);({target,controls}=createTarget());}
  gate.resolve();await operation;restorePanelDrafts(target);
  await flushUntil(() => target.reviews.some(dialog => dialog.open));
  const values=reviewValues(target);assert.deepEqual(new Set([values.current.end,values.recovered.end]),new Set(["","2027-06-04T17:00"]));
  const kept=copy(values.current),dialog=target.reviews.find(dialog => dialog.open);
  dialog.children.find(child => child.textContent === "Discard recovered edits").onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(target._guestIntervalDraft.values,kept);
  target.isConnected=false;retainPanelDrafts(target);const final=createTarget().target;restorePanelDrafts(final);
  assert.equal(final._guestIntervalDraft.values.end,kept.end);assert.equal(final.reviews.length,0);
  assert.equal(writes,1);
});

test("a partial interval acknowledgement preserves a newer reversion across replacement",async t => {
  const f=fixture(),source=f.create(),panels=[source],gate=deferred();
  source._page="capabilities";source._subsection="guest-mode";await routeAssetPromise(view,source);
  source._result={status:{active_from:null,active_until:null,indefinite:true}};
  const controls={"#guest-start":{value:""},"#guest-end":{value:""},"#guest-indefinite":{checked:true}};
  dialogs(source,controls);captureGuestInterval(source);const reset=documentStub();
  t.after(() => cleanup(panels,reset));let entered=false;
  source._hass.callWS=async () => {entered=true;await gate.promise;return {status:{state:"active"}};};
  source._refreshGuestModeMutation=async () => {};
  controls["#guest-start"].value="2027-06-04T12:00";captureGuestInterval(source);
  const operation=source._updateGuestMode();await flushUntil(() => entered);
  controls["#guest-start"].value="";captureGuestInterval(source);source.isConnected=false;retainPanelDrafts(source);
  const target=f.create();panels.push(target);target._page="capabilities";target._subsection="guest-mode";
  target._result={status:{active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true}};
  dialogs(target,{...controls,"#guest-start":{value:"2027-06-04T12:00"}});captureGuestInterval(target);
  gate.resolve();await operation;restorePanelDrafts(target);
  assert.equal(target._guestIntervalDraft.values.start,"","an incomplete canonical status cannot retire newer authored values");
});

for(const reply of ["malformed","lost"]) test(`a clean canonical interval read retires obsolete ${reply} evidence`,async t => {
  const f=fixture(),panel=f.create();panel._page="capabilities";panel._subsection="guest-mode";await routeAssetPromise(view,panel);
  panel._result={status:{active_from:null,active_until:null,indefinite:true}};
  const controls={"#guest-start":{value:""},"#guest-end":{value:""},"#guest-indefinite":{checked:true}};
  dialogs(panel,controls);captureGuestInterval(panel);t.after(() => cleanup([panel],() => {}));
  let actual;
  panel._hass.callWS=async request => {actual=request.active_from;if(reply === "lost") throw {code:3,message:"Connection lost"};return {};};
  await panel._updateGuestMode(true);assert.equal(panel._unsavedState.scopes.get("guest-interval").dirty(),false);
  const read=capturePageRead(panel);
  panel._result={status:{active_from:actual,active_until:null,indefinite:true},config:{guest_web_search:false},revision:"r1"};
  acceptPageRead(panel,panel._result,read);
  controls["#guest-start"].value=panel._dateTimeLocal(actual);captureGuestInterval(panel);
  assert.equal(panel._unsavedState.scopes.get("guest-interval").dirty(),false,"an authoritative read without newer edits must not invent dirty work");
});

for(const reply of ["partial","lost","not committed"]) for(const newer of ["none","revert","inactive end"]) test(`an accepted interval read settles ${reply} evidence and preserves ${newer}`,async t => {
  const f=fixture(),panel=f.create();panel._page="capabilities";panel._subsection="guest-mode";await routeAssetPromise(view,panel);
  panel._result={status:{active_from:null,active_until:null,indefinite:false},config:{guest_web_search:false},revision:"r0"};
  const controls={"#guest-start":{value:""},"#guest-end":{value:"",checkValidity:()=>true},"#guest-indefinite":{checked:false}};
  dialogs(panel,controls);captureGuestInterval(panel);t.after(() => cleanup([panel],() => {}));
  controls["#guest-start"].value="2027-06-04T12:00";controls["#guest-end"].value="2027-06-04T13:00";controls["#guest-indefinite"].checked=newer === "inactive end";
  captureGuestInterval(panel);const gate=deferred();let entered=false;
  panel._hass.callWS=async () => {entered=true;await gate.promise;if(reply !== "partial") throw {code:3,message:"Connection lost"};return {status:{state:"active"}};};
  panel._refreshGuestModeMutation=async () => {};
  const operation=panel._updateGuestMode();await flushUntil(() => entered);
  if(newer === "revert") {controls["#guest-start"].value="";controls["#guest-end"].value="";captureGuestInterval(panel);}
  if(newer === "inactive end") {controls["#guest-end"].value="2027-06-04T17:00";captureGuestInterval(panel);}
  gate.resolve();await operation;
  const before=copy(panel._guestIntervalDraft.values),scope=panel._unsavedState.scopes.get("guest-interval");
  const read=capturePageRead(panel);
  panel._result={...panel._result,status:reply === "not committed" ? {active_from:null,active_until:null,indefinite:false} : {active_from:"2027-06-04T12:00:00Z",active_until:newer === "inactive end" ? null : "2027-06-04T13:00:00Z",indefinite:newer === "inactive end"}};
  assert.equal(acceptPageRead(panel,panel._result,read),true);
  assert.equal(scope.dirty(),reply === "not committed" ? newer !== "revert" : newer !== "none","a completed read must establish the actual saved interval");
  assert.deepEqual(panel._guestIntervalDraft.values,before,"the authoritative read preserves post-submission authoring");
  assert.equal(panel._guestIntervalDraft.uncertainSubmission,null);
  controls["#guest-start"].value=reply === "not committed" ? "" : "2027-06-04T12:00";controls["#guest-end"].value=reply === "not committed" ? "" : "2027-06-04T13:00";controls["#guest-indefinite"].checked=reply !== "not committed" && newer === "inactive end";captureGuestInterval(panel);
  assert.equal(scope.dirty(),false,"returning to the now-known saved values clears unsaved state");
});

for(const route of ["section","timed"]) for(const boundary of ["current","incomplete","pending","owner","discard"]) test(`interval baseline recovery through ${route}: ${boundary}`,async t => {
  const f=fixture(),panel=f.create();panel._page="capabilities";panel._subsection="guest-mode";await routeAssetPromise(view,panel);
  panel._result={status:{active_from:null,active_until:null,indefinite:true},config:{guest_web_search:false},revision:"r0"};
  const controls={"#guest-start":{value:""},"#guest-end":{value:"",checkValidity:()=>true},"#guest-indefinite":{checked:true}};
  dialogs(panel,controls);captureGuestInterval(panel);panel._refreshGuestModeMutation=async () => {};
  panel._hass.callWS=async () => ({status:{state:"active"}});
  await panel._updateGuestMode(true);
  const scope=panel._unsavedState.scopes.get("guest-interval"),before=copy(panel._guestIntervalDraft.baseline);
  captureGuestInterval(panel);assert.ok(scope.needsRead(),"an unchanged render cannot certify a locally merged old status");
  let reads=0;
  const status={active_from:"2027-06-04T12:00:00Z",active_until:null,indefinite:true};
  panel._hass.callWS=async request => {if(request.action === "details") return {};reads++;return {config:{guest_web_search:false},revision:"r1",status:boundary === "incomplete" ? {state:"active"} : status};};
  if(boundary === "pending") panel._guestOperation=Promise.resolve();
  if(boundary === "discard") {scope.discard();assert.equal(scope.needsRead(),true,"Discard retains the authoritative read obligation");assert.equal(scope.dirty(),false);}
  if(boundary === "owner") panel._eocDraftGeneration=(panel._eocDraftGeneration||0)+1;
  const timers=[],realSet=globalThis.setTimeout,realClear=globalThis.clearTimeout;
  t.after(() => {stopLiveStatus(panel);globalThis.setTimeout=realSet;globalThis.clearTimeout=realClear;panel._guestOperation=null;cleanup([panel],() => {});});
  if(route === "section") {
    writeSectionCache(panel,panel._sectionCacheKey(view),copy(panel._result));
    await panel._loadSectionData();
  } else {
    globalThis.setTimeout=callback => {timers.push(callback);return timers.length;};globalThis.clearTimeout=() => {};
    watchTimedFeatureStatus(panel);assert.equal(timers.length,1);await timers.shift()();
  }
  if(["current","discard"].includes(boundary)) {
    assert.equal(reads,1);assert.equal(scope.needsRead(),false);assert.equal(scope.dirty(),false);
    assert.equal(controls["#guest-start"].value,"2027-06-04T12:00","the native input follows accepted normalization");
  } else if(["incomplete","pending"].includes(boundary)) {
    assert.equal(scope.needsRead(),true);assert.deepEqual(panel._guestIntervalDraft.baseline,before);
  } else assert.equal(scope.needsRead(),false);
});

for(const first of ["partial","lost"]) for(const field of ["start","end","indefinite"]) test(`a later unresolved ${field} reversion survives an earlier ${first} interval write`,async t => {
  const f=fixture(),panel=f.create();panel._page="capabilities";panel._subsection="guest-mode";await routeAssetPromise(view,panel);
  const original={active_from:"2027-06-04T12:00:00Z",active_until:"2027-06-04T15:00:00Z",indefinite:false};
  panel._result={status:original,config:{guest_web_search:false},revision:"r0"};
  const controls={"#guest-start":{value:"2027-06-04T12:00"},"#guest-end":{value:"2027-06-04T15:00",checkValidity:()=>true},"#guest-indefinite":{checked:false}};
  dialogs(panel,controls);captureGuestInterval(panel);panel._refreshGuestModeMutation=async () => {};let writes=0,saved;
  t.after(() => cleanup([panel],() => {}));
  panel._hass.callWS=async request => {
    if(++writes === 1) {
      saved={active_from:request.local_times?.active_from ? `${request.active_from}:00Z` : request.active_from,active_until:request.indefinite ? null : request.local_times?.active_until ? `${request.active_until}:00Z` : request.active_until,indefinite:request.indefinite};
      if(first === "partial") return {status:{state:"active"}};
    }
    throw {code:3,message:"Connection lost"};
  };
  const control=controls[`#guest-${field}`],property=field === "indefinite" ? "checked" : "value",before=control[property];
  control[property]=field === "indefinite" ? true : "2027-06-04T14:00";captureGuestInterval(panel);await panel._updateGuestMode();
  control[property]=before;captureGuestInterval(panel);await panel._updateGuestMode();assert.equal(writes,2);
  const read=capturePageRead(panel);panel._result={...panel._result,status:saved};acceptPageRead(panel,panel._result,read);
  assert.equal(panel._guestIntervalDraft.values[field],before,"the second submission is authored even when it equals the oldest baseline");
  assert.equal(panel._unsavedState.scopes.get("guest-interval").dirty(),true);
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
