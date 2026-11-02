// Consume the real Python handler's serialized error and next admission.
import assert from "node:assert/strict";
import {bindBroadcast} from "../custom_components/extended_openai_conversation_responses/frontend/overview-broadcast.js";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const {rejected, admitted, retryToken} = JSON.parse(input);
assert.equal(rejected.success, false);
assert.equal(admitted.success, true);
const controls = new Map();
for (const selector of ["#broadcast-card", "#broadcast-message", "#broadcast-send"]) {
  controls.set(selector, {value:"Dinner", addEventListener(event, callback) { this[event] = callback; }});
}
const snapshot = {enabled:true, retry_token:retryToken, catalog:{satellites:admitted.result.targets.map(id => ({id, name:id}))}, history:[], pending:[]};
const requests = [], notices = [];
const connection = {user:{id:"owner"}, callWS:async request => {
  if (request.action === "snapshot") return snapshot;
  requests.push(request);
  if (requests.length === 1) throw rejected.error;
  return admitted.result;
}};
const panel = {hass:connection, _hass:connection, _broadcastMessage:"Dinner", _broadcastWholeHome:true,
  _e:String, _titleCase:String, _viewKey:() => "overview",
  shadowRoot:{querySelector:selector => controls.get(selector) || null, querySelectorAll:() => []},
  _toast:message => notices.push(message), _confirm:() => { throw Error("a rejected first send must not require new-intent confirmation"); },
};
await bindBroadcast(panel, Promise.resolve(snapshot));
const button = controls.get("#broadcast-send");
await button.click({currentTarget:button});
assert.equal(panel._broadcastMessage, "Dinner");
assert.equal(panel._broadcastAttempt, null);
assert.match(notices[0], /not queued.*Retry in 600 seconds/);
await button.click({currentTarget:button});
assert.equal(requests.length, 2);
assert.notEqual(requests[0].operation_id, requests[1].operation_id);
assert.equal(panel._broadcastMessage, "");
assert.equal(panel._broadcastAttempt, null);
