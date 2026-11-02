import assert from "node:assert/strict";
import test from "node:test";

import {bindBroadcast} from "../custom_components/extended_openai_conversation_responses/frontend/overview-broadcast.js";

const target = "assist_satellite.kitchen";
const token = `${"a".repeat(32)}:100000`;

async function composer(send) {
  const controls = new Map();
  const control = selector => {
    if (!controls.has(selector)) controls.set(selector, {
      value:"", disabled:false, innerHTML:"", dataset:{},
      addEventListener(name, listener) { this[name] = listener; },
    });
    return controls.get(selector);
  };
  for (const selector of ["#broadcast-card", "#broadcast-message", "#broadcast-send"]) control(selector);
  control("#broadcast-message").value = "Dinner";
  const snapshot = {enabled:true, can_manage:true, retry_token:token,
    catalog:{satellites:[{id:target, name:"Kitchen"}]}, history:[], pending:[]};
  const confirmations = [];
  const connection = {user:{id:"owner"}, callWS:async request => request.action === "snapshot" ? snapshot : send(request)};
  const panel = {
    hass:connection, _hass:connection, _broadcastMessage:"Dinner", _broadcastWholeHome:true,
    _e:String, _titleCase:String, _viewKey:() => "overview", _eocAuthenticationGeneration:0,
    shadowRoot:{querySelector:selector => controls.get(selector) || null, querySelectorAll:() => []},
    _toast() {}, _confirm:async (...args) => { confirmations.push(args); return false; },
  };
  await bindBroadcast(panel, Promise.resolve(snapshot));
  const click = async () => {
    const button = control("#broadcast-send");
    await button.click({currentTarget:button});
  };
  return {panel, click, confirmations, snapshot};
}

test("an authoritative retry rejection cannot erase an earlier uncertain admission", async () => {
  const requests = [];
  const {panel, click, confirmations} = await composer(async request => {
    requests.push(request);
    if (requests.length === 1) throw Error("acknowledgement lost after admission");
    if (requests.length === 2) throw {code:"invalid_request", message:"Caller no longer has control permission"};
    return {id:"accidental-repeat", targets:[target]};
  });

  await click();
  const original = panel._broadcastAttempt;
  await click();
  assert.equal(requests[1].operation_id, requests[0].operation_id, "the retry uses the original operation identity");
  assert.equal(panel._broadcastAttempt, original, "a rejected retry does not settle the original lost acknowledgement");

  await click();
  assert.equal(confirmations.length, 1, "sending as a new operation requires explicit intent");
  assert.equal(requests.length, 2, "declining confirmation must not send an intentional repeat");
});

test("refreshing a snapshot cannot replace an uncertain operation's identity", async () => {
  const requests = [];
  const {panel, click, snapshot} = await composer(async request => {
    requests.push(request);
    throw Error("connection lost");
  });
  await click();
  snapshot.retry_token = `${"b".repeat(32)}:200000`;
  await bindBroadcast(panel, Promise.resolve(snapshot));
  await click();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].operation_id, requests[0].operation_id);
  assert.deepEqual(requests[1], requests[0]);
});

test("a malformed acknowledgement remains bound to the original retry identity", async () => {
  const requests = [];
  const {panel, click} = await composer(async request => {
    requests.push(request);
    return requests.length === 1 ? {status:"queued"} : {id:"first-admission", targets:[target]};
  });
  await click();
  assert.equal(panel._broadcastMessage, "Dinner");
  await click();
  assert.equal(requests[1].operation_id, requests[0].operation_id);
  assert.equal(panel._broadcastMessage, "");
  assert.equal(panel._broadcastAttempt, null);
});

test("an authentication round trip cannot silently turn an uncertain retry into a new send", async () => {
  const requests = [];
  const {panel, click, confirmations} = await composer(async request => {
    requests.push(request);
    throw Error("acknowledgement lost after admission");
  });
  await click();
  // Management keeps authored drafts through a temporary owner transition.
  // Returning to the original user establishes a new authentication lifetime.
  panel._eocAuthenticationGeneration = 2;
  await click();

  assert.equal(confirmations.length, 1);
  assert.equal(requests.length, 1, "declining a new send preserves the original ambiguity");
  assert.equal(panel._broadcastMessage, "Dinner");
});
