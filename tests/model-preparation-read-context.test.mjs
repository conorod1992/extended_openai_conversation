import assert from "node:assert/strict";
import test from "node:test";
import {changeConfigurationModel} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js";
import {recordReasoningChoice} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-controls.js";
import {captureConfigurationSubmission} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";

function preparing(config, field = "chat_model") {
  let resolve;
  const lookup = new Promise(done => {resolve = done;});
  const panel = {
    _agentId:"agent", _draftAgentId:"agent", _draft:{...config},
    _configData:{config:{...config, reasoning_effort:"xhigh"}}, _result:{},
    hass:{user:{id:"admin"}}, _selectedAgent:() => ({entry_id:"entry"}),
    shadowRoot:{querySelector:() => null, dispatchEvent() {}},
    _setConfigDirty(value) { this._configDirty = value; }, _render() {},
    _hass:{callWS:() => lookup}, _call:async () => ({valid:true}),
  };
  const pending = changeConfigurationModel(panel, {dataset:{config:field}, value:config[field]});
  return {panel, pending, resolve:metadata => resolve({model_metadata:metadata || {reasoning:{supported:true, efforts:["minimal","low","high"]}, recommended_profile:{reasoning_effort:"minimal"}}})};
}

for (const {name, config, field, savedConfig, retires} of [
  {name:"native full read", config:{chat_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"gpt-5", reasoning_effort:"low"}, retires:true},
  {name:"ordinary control copy", config:{chat_model:"gpt-5", reasoning_effort:"low"}, retires:false},
  {name:"unrelated model read", config:{chat_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"gpt-5.2", reasoning_effort:"low"}, retires:false},
  {name:"retention projection", config:{chat_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{usage_run_retention_days:7}, retires:false},
  {name:"Azure deployment read", config:{chat_model:"new-deployment", azure_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"new-deployment", azure_model:"gpt-5", reasoning_effort:"low"}, retires:true},
  {name:"Azure wrong underlying model", config:{chat_model:"new-deployment", azure_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"new-deployment", azure_model:"gpt-4o", reasoning_effort:"low"}, retires:false},
  {name:"Azure wrong deployment", config:{chat_model:"new-deployment", azure_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"old-deployment", azure_model:"gpt-5", reasoning_effort:"low"}, retires:false},
  {name:"Azure underlying model read", field:"azure_model", config:{chat_model:"deployment", azure_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{chat_model:"deployment", azure_model:"gpt-5", reasoning_effort:"low"}, retires:true},
  {name:"Azure incomplete projection", field:"azure_model", config:{chat_model:"deployment", azure_model:"gpt-5", reasoning_effort:"low"}, savedConfig:{azure_model:"gpt-5"}, retires:false},
]) test(`${name} ${retires ? "retires" : "does not retire"} pending model defaults`, async () => {
  const {panel,pending,resolve} = preparing(config, field);
  const previous = panel._draft, next = {...previous};
  const intent = panel._eocModelIntent, token = panel._eocModelChangeToken;
  panel._eocPendingModelPreparation.rebindDraft(previous, next, {savedConfig});
  panel._draft = next;
  assert.equal(Boolean(panel._eocPendingModelPreparation), !retires);
  assert.equal(panel._eocModelChangeToken, token + Number(retires));
  assert.equal(panel._eocModelIntent, intent, "accepted reads do not invent another authored intent");
  resolve();
  await pending;
  assert.equal(panel._draft.reasoning_effort, retires ? "low" : "minimal");
});

test("an accepted model read preserves a newer explicit reasoning choice", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  recordReasoningChoice(panel);
  panel._draft.reasoning_effort = "high";
  const previous = panel._draft, next = {...previous};
  panel._eocPendingModelPreparation.rebindDraft(previous, next, {savedConfig:{chat_model:"gpt-5", reasoning_effort:"low"}});
  panel._draft = next;
  resolve();
  await pending;
  assert.equal(panel._draft.reasoning_effort, "high");
});

for (const owner of ["user", "entry", "agent"]) test(`a different ${owner} cannot retire another owner's preparation`, async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const preparation = panel._eocPendingModelPreparation, token = panel._eocModelChangeToken;
  if (owner === "user") panel.hass.user.id = "other";
  if (owner === "entry") panel._selectedAgent = () => ({entry_id:"other"});
  if (owner === "agent") panel._agentId = "other";
  preparation.rebindDraft(panel._draft, {...panel._draft}, {savedConfig:{chat_model:"gpt-5", reasoning_effort:"low"}});
  assert.equal(panel._eocPendingModelPreparation, preparation);
  assert.equal(panel._eocModelChangeToken, token);
  resolve();
  await pending;
  assert.equal(panel._draft.reasoning_effort, "low");
});

for (const removal of [false,true]) test(`an acknowledged intent replaces only its later derived effort, removed=${removal}`, async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel);
  resolve(removal ? {reasoning:{supported:false}} : undefined);
  await pending;
  const intent = panel._eocModelIntent;
  assert.equal(intent.reasoningDefaultRevision, 1);
  assert.equal(Object.hasOwn(panel._draft,"reasoning_effort"), !removal);
  assert.equal(intent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission), true);
  assert.equal(panel._draft.reasoning_effort, "low");
});

test("an acknowledged absence removes the same intent's derived effort", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel);
  resolve();
  await pending;
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"gpt-5"},submission), true);
  assert.equal(Object.hasOwn(panel._draft,"reasoning_effort"), false);
});

test("a default applied before submission is not a later derived edit", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  resolve();
  await pending;
  const submission = captureConfigurationSubmission(panel);
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission), false);
  assert.equal(panel._draft.reasoning_effort, "minimal");
});

for (const reaffirm of [false,true]) test(`a newer explicit effort prevents derived acknowledgement repair, reaffirm=${reaffirm}`, async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel);
  resolve();
  await pending;
  recordReasoningChoice(panel);
  panel._draft.reasoning_effort = reaffirm ? "minimal" : "high";
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission), false);
  assert.equal(panel._draft.reasoning_effort, reaffirm ? "minimal" : "high");
});

test("different current effort is not mistaken for the last derived value", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel);
  resolve();
  await pending;
  panel._draft.reasoning_effort = "high";
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission), false);
  assert.equal(panel._draft.reasoning_effort,"high");
});

test("the effective comparison owns a newer persisted reasoning value", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5", reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel);
  resolve();
  await pending;
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission,{chat_model:"gpt-5",reasoning_effort:"high"}), true);
  assert.equal(panel._draft.reasoning_effort,"high");
});

test("a read-adopted Azure underlying model uses its comparison's reasoning value", async () => {
  const {panel,pending,resolve} = preparing({chat_model:"deployment",azure_model:"gpt-5",reasoning_effort:"low"},"azure_model");
  const submission = captureConfigurationSubmission(panel);
  resolve();
  await pending;
  panel._draft.azure_model = "gpt-6-astra";
  assert.equal(panel._eocModelIntent.acknowledgeReasoning(panel,{chat_model:"deployment",azure_model:"gpt-5",reasoning_effort:"low"},submission,{chat_model:"deployment",azure_model:"gpt-6-astra",reasoning_effort:"high"}), true);
  assert.equal(panel._draft.reasoning_effort,"high");
});

for (const owner of ["user","entry","agent","intent","model"]) test(`a different ${owner} cannot repair another intent's derived effort`, async () => {
  const {panel,pending,resolve} = preparing({chat_model:"gpt-5",reasoning_effort:"low"});
  const submission = captureConfigurationSubmission(panel), intent = panel._eocModelIntent;
  resolve();
  await pending;
  if (owner === "user") panel.hass.user.id = "other";
  if (owner === "entry") panel._selectedAgent = () => ({entry_id:"other"});
  if (owner === "agent") panel._agentId = "other";
  if (owner === "intent") panel._eocModelIntent = {};
  if (owner === "model") panel._draft.chat_model = "gpt-5-mini";
  assert.equal(intent.acknowledgeReasoning(panel,{chat_model:"gpt-5",reasoning_effort:"low"},submission), false);
  assert.equal(panel._draft.reasoning_effort,"minimal");
});
