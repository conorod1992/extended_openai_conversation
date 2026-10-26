import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, flushUntil} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {changeConfigurationModel} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js";
import {recordReasoningChoice} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-controls.js";

const metadata = {model_metadata:{reasoning:{supported:true, efforts:["minimal", "low", "high"]}, recommended_profile:{reasoning_effort:"minimal"}}};

function preparing({holdValidation = false} = {}) {
  const {create} = fixture(), panel = create(), lookup = deferred(), validation = deferred();
  panel._draft = {chat_model:"gpt-5", reasoning_effort:"low"};
  panel._configData = {config:{chat_model:"gpt-5", reasoning_effort:"low"}};
  panel._result = {model_capabilities:{marker:"accepted-current"}};
  const counts = {lookups:0, validations:0, renders:0};
  panel._render = () => {counts.renders++;};
  panel._hass.callWS = async request => {
    assert.equal(request.action, "lookup"); counts.lookups++; return lookup.promise;
  };
  panel._call = async (section, action) => {
    assert.equal(section, "configuration"); assert.equal(action, "validate");
    counts.validations++;
    return holdValidation ? validation.promise : {valid:true, model_capabilities:{marker:"new-current"}};
  };
  const pending = changeConfigurationModel(panel, {dataset:{config:"chat_model"}, value:"gpt-5"});
  return {panel, counts, lookup, validation, pending};
}

function userRoundTrip(panel, intermediate) {
  const original = panel.hass;
  panel.hass = {...original, user:intermediate == null ? undefined : {id:intermediate}};
  panel.hass = original;
}

for (const intermediate of ["different-user", null]) for (const failed of [false, true]) for (const explicitChoice of [false, true]) {
  test(`retired model lookup cannot apply defaults after actual user round trip via ${intermediate ?? "unknown"}, failed=${failed}, explicit=${explicitChoice}`, async t => {
    const {panel, counts, lookup, pending} = preparing();
    t.after(async () => {lookup.resolve(metadata); await pending; retire(panel);});
    const draft = panel._draft, intent = panel._eocModelIntent, capabilities = panel._result.model_capabilities;
    userRoundTrip(panel, intermediate);
    if (explicitChoice) {recordReasoningChoice(panel); draft.reasoning_effort = "high";}
    if (failed) lookup.reject(Error("Retired provider failure")); else lookup.resolve(metadata);
    await pending;
    assert.equal(panel._draft, draft);
    assert.equal(panel._eocModelIntent, intent, "retiring an operation must retain the authored model intent");
    assert.equal(draft.reasoning_effort, explicitChoice ? "high" : "low");
    assert.equal(panel._result.model_capabilities, capabilities);
    assert.equal(panel._eocPendingModelPreparation, null);
    assert.deepEqual(panel.toasts, []);
    assert.deepEqual(counts, {lookups:1, validations:0, renders:0});
  });
}

for (const failed of [false, true]) {
  test(`model validation cannot publish after its lookup completed before auth retirement, failed=${failed}`, async t => {
    const {panel, counts, lookup, validation, pending} = preparing({holdValidation:true});
    t.after(async () => {lookup.resolve(metadata); validation.resolve({valid:true}); await pending; retire(panel);});
    lookup.resolve(metadata);
    await flushUntil(() => counts.validations === 1);
    assert.equal(panel._draft.reasoning_effort, "minimal", "defaults applied while the original operation owned its draft");
    const draft = panel._draft, intent = panel._eocModelIntent, capabilities = panel._result.model_capabilities;
    userRoundTrip(panel, "different-user");
    recordReasoningChoice(panel); draft.reasoning_effort = "high";
    if (failed) validation.reject(Error("Old validation failure"));
    else validation.resolve({valid:true, model_capabilities:{marker:"obsolete"}});
    await pending;
    assert.equal(panel._draft, draft);
    assert.equal(draft.reasoning_effort, "high");
    assert.equal(panel._eocModelIntent, intent);
    assert.equal(panel._result.model_capabilities, capabilities);
    assert.deepEqual(panel.toasts, []);
    assert.equal(counts.lookups, 1); assert.equal(counts.validations, 1); assert.equal(counts.renders, 0);
  });
}

test("same-user HA state updates allow the original model preparation to finish", async t => {
  const {panel, counts, lookup, pending} = preparing();
  t.after(async () => {lookup.resolve(metadata); await pending; retire(panel);});
  const draft = panel._draft, intent = panel._eocModelIntent;
  for (let i = 0; i < 4; i++) panel.hass = {...panel.hass, states:{[`sensor.update_${i}`]:{state:String(i)}}};
  lookup.resolve(metadata); await pending;
  assert.equal(panel._draft, draft);
  assert.equal(panel._eocModelIntent, intent);
  assert.equal(draft.reasoning_effort, "minimal");
  assert.deepEqual(panel._result.model_capabilities, {marker:"new-current"});
  assert.deepEqual(panel.toasts, []);
  assert.equal(counts.lookups, 1); assert.equal(counts.validations, 1); assert.equal(counts.renders, 1);
});
