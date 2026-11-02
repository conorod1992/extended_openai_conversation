import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, edit, retainPanelDrafts, restorePanelDrafts} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {bindBroadcast} from "../custom_components/extended_openai_conversation_responses/frontend/overview-broadcast.js";
import {watchLiveStatus, stopLiveStatus} from "../custom_components/extended_openai_conversation_responses/frontend/management-live-status.js";
import {startOverviewBroadcastSnapshot} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page.js";

for (const roundTrip of [false, true]) for (const failed of [false, true]) {
  test(`Broadcast read cannot cross authentication changes, ABA=${roundTrip}, failed=${failed}`, async t => {
    const {create} = fixture(), panel = create(), gate = deferred();
    t.after(() => retire(panel));
    panel.hass = panel.hass;
    const original = panel.hass;
    panel._page = "overview"; panel._subsection = null;
    const host = {innerHTML:"Original owner's content"};
    panel.shadowRoot.querySelector = selector => selector === "#broadcast-card" ? host : null;
    const pending = bindBroadcast(panel, gate.promise);
    panel.hass = {...original, user:{id:"restricted-actor", is_admin:false}};
    assert.equal(panel._canAccessView("assistant", "basics"), false);
    if (roundTrip) panel.hass = original;
    const before = host.innerHTML;
    if (failed) gate.reject(Error("Previous owner's private error"));
    else gate.resolve({enabled:true, can_manage:true, catalog:{satellites:[]}, pending:[],
      history:[{id:"private", message:"Previous owner's announcement", deliveries:{}}]});
    await pending;
    assert.equal(host.innerHTML, before, "retired success and error must not publish, even after returning to the same owner");
  });
}

test("same-user updates preserve the Broadcast read and authored configuration", async t => {
  const {create} = fixture(), panel = create(), gate = deferred();
  t.after(() => retire(panel));
  panel.hass = panel.hass;
  const draft = panel._draft;
  panel._page = "overview"; panel._subsection = null;
  const host = {innerHTML:""};
  panel.shadowRoot.querySelector = selector => selector === "#broadcast-card" ? host : null;
  const pending = bindBroadcast(panel, gate.promise);
  panel.hass = {...panel.hass, states:{"sensor.update":{state:"1"}}};
  gate.resolve({enabled:true, can_manage:true, catalog:{satellites:[]}, pending:[], history:[]});
  await pending;
  assert.ok(host.innerHTML.includes("Broadcast"));
  assert.equal(panel._draft, draft);
});

test("an in-flight live status watch cannot publish after an authentication round trip", async t => {
  const {create} = fixture(), panel = create(), gate = deferred();
  panel.hass = panel.hass;
  const timers = [], originalTimer = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
  globalThis.setTimeout = callback => { timers.push(callback); return timers.length; };
  globalThis.clearTimeout = () => {};
  t.after(() => { stopLiveStatus(panel); globalThis.setTimeout = originalTimer; globalThis.clearTimeout = originalClear; retire(panel); });
  let published = false;
  watchLiveStatus(panel, "test", {view:panel._viewKey(), delay:200, maxRefreshes:1, refresh:async current => {
    await gate.promise;
    if (current()) published = true;
  }});
  const refresh = timers[0]();
  const original = panel.hass;
  panel.hass = {...original, user:{id:"other-account"}};
  panel.hass = original;
  gate.resolve();
  await refresh;
  assert.equal(published, false);
});

test("lazy Broadcast binding cannot adopt a read from the previous authentication generation", async t => {
  const {create} = fixture(), panel = create(), gate = deferred();
  t.after(() => retire(panel));
  panel.hass = panel.hass;
  panel._page = "overview"; panel._subsection = null;
  panel.hass.callWS = () => gate.promise;
  const snapshot = startOverviewBroadcastSnapshot(panel), original = panel.hass;
  panel.hass = {...original, user:{id:"other-account"}};
  panel.hass = original;
  const host = {innerHTML:"Fresh owner presentation"};
  panel.shadowRoot.querySelector = selector => selector === "#broadcast-card" ? host : null;
  const bound = bindBroadcast(panel, snapshot);
  gate.resolve({enabled:true, can_manage:true, catalog:{satellites:[]}, pending:[], history:[]});
  await bound;
  assert.equal(host.innerHTML, "Fresh owner presentation");
});

test("a panel disconnected under another account retains its draft only for the original author", t => {
  const {create, agentA} = fixture(), source = create();
  source.hass = source.hass;
  const user = source.hass.user.id;
  edit(source, "prompt", "Private retained authoring");
  source.hass = {...source.hass, user:{id:"different-account"}};
  source.isConnected = false;
  retainPanelDrafts(source);
  const other = create(agentA, "different-account");
  other.hass = other.hass;
  restorePanelDrafts(other);
  assert.notEqual(other._draft.prompt, "Private retained authoring");
  const original = create(agentA, user);
  original.hass = original.hass;
  restorePanelDrafts(original);
  assert.equal(original._draft.prompt, "Private retained authoring");
  t.after(() => retire(source, other, original));
});

test("a retired Broadcast prefetch can reject before its lazy binder attaches", async t => {
  const {create} = fixture(), panel = create(), gate = deferred();
  t.after(() => retire(panel));
  panel.hass = panel.hass;
  panel.hass.callWS = () => gate.promise;
  const snapshot = startOverviewBroadcastSnapshot(panel), original = panel.hass;
  panel.hass = {...original, user:{id:"other-account"}};
  gate.reject(Error("Retired prefetch failed"));
  await new Promise(resolve => setImmediate(resolve));
  panel.hass = original;
  const host = {innerHTML:"Current owner content"};
  panel.shadowRoot.querySelector = selector => selector === "#broadcast-card" ? host : null;
  await bindBroadcast(panel, snapshot);
  assert.equal(host.innerHTML, "Current owner content");
});
