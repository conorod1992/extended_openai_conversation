import assert from "node:assert/strict";
import {lookupModelData} from "../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js";
import {modelFieldPresentation} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js";

for (const host of ["_result", "_configData"]) {
  const calls = [];
  const capabilities = {reasoning:{supported:true, efforts:["low", "medium", "high"]}};
  const panel = {
    [host]:{api_provider:"azure", config:{chat_model:"o3-pro", reasoning_effort:"high"}},
    _draft:{chat_model:"o3-pro", reasoning_effort:"high"},
    _hass:{async callWS(message) {calls.push(message); return {model_capabilities:capabilities};}},
  };
  await lookupModelData(panel, "o3-pro");
  assert.equal(calls[0].provider, "azure");
  assert.equal(panel._modelCatalogData.requested_provider, "azure");
  const field = modelFieldPresentation(panel, "reasoning_effort", "high");
  assert.equal(field.visible, true);
  assert.deepEqual(field.efforts, ["low", "medium", "high"]);
  panel[host].api_provider = "openai";
  panel._result = {api_provider:"openai", config:panel._draft, model_capabilities:{reasoning:{supported:false}}};
  assert.equal(modelFieldPresentation(panel, "reasoning_effort", "high").visible, false);
  await lookupModelData(panel, "o3-pro");
  assert.equal(calls[1].provider, undefined);
}
