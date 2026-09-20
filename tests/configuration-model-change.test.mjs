import assert from "node:assert/strict";
import {changeConfigurationModel} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js";

const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; };
const first = deferred();
const calls = [];
const panel = {
  _agentId:"a", _draftAgentId:"a", _draft:{chat_model:"old"},
  _configData:{config:{chat_model:"baseline"}}, _result:{},
  shadowRoot:{dispatchEvent() {}}, _setConfigDirty(value) { this._configDirty = value; },
  _render() { calls.push("render"); },
  _hass:{callWS:async ({model}) => model === "old" ? first.promise : {model_capabilities:{new:true}, model_metadata:{reasoning:{supported:true, efforts:["low","high"]},recommended_profile:{reasoning_effort:"high"}}}},
  _call:async () => { calls.push("validate"); return {valid:true, model_capabilities:{new:true}}; },
};
const stale = changeConfigurationModel(panel, {value:"old"});
panel._draft.chat_model = "new";
await changeConfigurationModel(panel, {value:"new"});
first.resolve({model_capabilities:{stale:true}, model_metadata:{reasoning:{supported:false}}});
await stale;
assert.equal(panel._draft.chat_model, "new");
assert.equal(panel._draft.reasoning_effort, "high");
assert.equal(panel._modelCatalogData.requested_model, "new");
assert.deepEqual(panel._result.model_capabilities, {new:true});
assert.deepEqual(calls, ["validate","render"]);

const switched = deferred();
panel._hass.callWS = () => switched.promise;
panel._draft.chat_model = "pending";
const pending = changeConfigurationModel(panel, {value:"pending"});
panel._agentId = "b";
panel._draft = {chat_model:"other-agent"};
panel._result = {model_capabilities:{other:true}};
switched.resolve({model_capabilities:{stale:true}});
await pending;
assert.deepEqual(panel._draft, {chat_model:"other-agent"});
assert.deepEqual(panel._result.model_capabilities, {other:true});
assert.deepEqual(calls, ["validate","render"]);
