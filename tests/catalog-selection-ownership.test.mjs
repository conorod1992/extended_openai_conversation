import assert from "node:assert/strict";
import test from "node:test";
import {fixture,retire,captureConfigurationSubmission} from "./management-ownership-harness.mjs";
import {captureCatalogSelectionIntent} from "../custom_components/extended_openai_conversation_responses/frontend/management-catalog-ownership.js";

for(const fails of [false,true]) test(`an explicitly authorized catalog transition owns its new continuation, failure=${fails}`,async () => {
  const {state,create,agentB}=fixture(),panel=create();
  panel._error=null;
  const owner=captureConfigurationSubmission(panel),original=panel._hass.callWS;
  panel._hass.callWS=request => request.action === "agents" && !request.section
    ? Promise.resolve({is_admin:true,agents:structuredClone(state.agents),scopes:[]}) : original(request);
  let loaded=0;
  panel._loadSection=async () => {loaded++; if(fails) throw new Error("Current continuation failed");};
  await panel._loadAgents(agentB,owner);
  assert.equal(panel._agentId,agentB);
  assert.equal(loaded,1,"an authorized owner change must still load the selected assistant");
  assert.equal(panel._error,fails ? "Current continuation failed" : null);
  retire(panel);
});

test("a metadata-only configuration envelope does not retire clean catalog selection",async () => {
  const {state,create,agentB}=fixture(),panel=create();
  const intent=captureCatalogSelectionIntent(panel),original=panel._hass.callWS;
  panel._configData={...panel._configData,model_capabilities:{current:true}};
  panel._draft=structuredClone(panel._draft);
  panel._hass.callWS=request => request.action === "agents" && !request.section
    ? Promise.resolve({is_admin:true,agents:structuredClone(state.agents),scopes:[]}) : original(request);
  let loaded=0;panel._loadSection=async () => {loaded++;};
  await panel._loadAgents(agentB,null,intent);
  assert.equal(panel._agentId,agentB);
  assert.equal(loaded,1);
  retire(panel);
});

test("an identity-only restore refresh remains valid after its approved draft clear",async () => {
  const {state,create}=fixture(),panel=create();
  const owner={agentId:panel._agentId,entryId:panel._selectedAgent().entry_id},original=panel._hass.callWS;
  panel._clearConfigDraft();
  panel._hass.callWS=request => request.action === "agents" && !request.section
    ? Promise.resolve({is_admin:true,agents:structuredClone(state.agents),scopes:[]}) : original(request);
  let loaded=0;panel._loadSection=async () => {loaded++;};
  await panel._loadAgents(owner.agentId,owner);
  assert.equal(panel._agentId,owner.agentId);
  assert.equal(loaded,1);
  retire(panel);
});
