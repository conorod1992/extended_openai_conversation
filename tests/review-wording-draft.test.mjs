import assert from "node:assert/strict";
import {bindPageDrafts, initializePageDraft, currentPageScope, savePageChanges} from "../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js";

const canonical = {value:"turn on"};
const alternative = {value:"enable", setCustomValidity(value){this.error=value;}, setAttribute(){}};
const row = {querySelector: selector => selector === ".wording-canonical" ? canonical : selector === ".wording-alternatives" ? alternative : null};
const fields = new Map([
  ["#rules-default-word-forms",{checked:true}], ["#rules-default-wording",{checked:true}],
  ["#rules-default-fuzzy",{checked:false}], ["#rules-default-threshold",{value:"90"}],
]);
const root = new EventTarget();
root.querySelector = selector => fields.get(selector) || null;
root.querySelectorAll = selector => selector === ".wording-group" ? [row] : [];
const requests = [], toasts = [];
const panel = {
  shadowRoot:root, _agentId:"agent", _viewKey:()=>"capabilities/request-rules",
  _result:{defaults:{word_forms:true, wording_alternatives:true, fuzzy:false, fuzzy_threshold:90}, wording_groups:[{canonical:"turn on", alternatives:["enable"]}], revision:"one"},
  _call:async (section, action, payload) => {requests.push(payload); return {...payload, revision:"two"};},
  _toast:(...args)=>toasts.push(args), _render:()=>assert.fail("must preserve current form"),
};
initializePageDraft(panel);
bindPageDrafts(panel);
canonical.value = "switch on";
root.dispatchEvent(new Event("input"));
await Promise.resolve();
alternative.value = '"would you, please';
root.dispatchEvent(new Event("input"));
await Promise.resolve();
assert.equal(currentPageScope(panel).dirty(), true);
assert.equal(panel._rulesSettingsDraft.wording_groups[0].alternatives_raw, alternative.value);
assert.match(alternative.error, /quotation/);
assert.equal(await savePageChanges(panel), false);
assert.equal(requests.length, 0);
assert.equal(alternative.value, '"would you, please');
assert.match(toasts.at(-1)[0], /quotation/);
alternative.value += '"';
// Save must read the visible edit even before its input microtask has run.
root.dispatchEvent(new Event("input"));
assert.equal(await savePageChanges(panel), true);
assert.deepEqual(requests[0].wording_groups, [{canonical:"switch on", alternatives:["would you, please"]}]);
assert.equal(alternative.error, "");
assert.equal(currentPageScope(panel).dirty(), false);
