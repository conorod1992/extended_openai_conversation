import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

import {modelFieldNotes, modelFieldPresentation} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js";
import {pickerModels} from "../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js";
import {buildSetupHealth} from "../custom_components/extended_openai_conversation_responses/frontend/overview-health.js";

const catalog = JSON.parse(readFileSync(new URL("../custom_components/extended_openai_conversation_responses/model_catalog.json", import.meta.url), "utf8"));
const retiring = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"];
const freshChoices = pickerModels({catalog_models: catalog.models});
assert.equal(freshChoices.some(({id}) => retiring.includes(id)), false);

for (const model of retiring) {
  const metadata = catalog.models.find(({id}) => id === model);
  const config = Object.freeze({chat_model: model, api_mode: "responses", temperature: 0.4});
  const selected = pickerModels({catalog_models: catalog.models}, model);
  assert.equal(selected.find(({id}) => id === model)?.status, "deprecated");
  const panel = {
    _draft: config,
    _modelCatalogData: {requested_model: model, model_capabilities: metadata, catalog_models: selected},
    _e: String,
  };
  const presentation = modelFieldPresentation(panel, "chat_model", model);
  assert.match(presentation.note, /Deprecated model/);
  assert.match(presentation.note, /remains selected/);
  assert.match(presentation.note, /will not be changed automatically/);
  assert.match(presentation.note, /January 14, 2027/);
  assert.match(presentation.note, /gpt-6-luna/);
  const html = modelFieldNotes(panel, presentation);
  assert.ok(html.includes(`value="${model}"`));
  assert.match(html, /— Deprecated/);
  assert.equal(panel._draft, config);
  assert.equal(config.chat_model, model);

  for (const [shutdownReached, confirmed, expectedState, expectedValue] of [
    [false, false, "warning", "Scheduled for shutdown · 2027-01-14"],
    [true, false, "warning", "Shutdown date reached"],
    [true, true, "error", "Retired model unavailable"],
  ]) {
    const health = buildSetupHealth({
      model_lifecycle: {...metadata, model, shutdown_reached: shutdownReached, confirmed_unavailable: confirmed},
    });
    const check = health.checks.find(({id}) => id === "model_lifecycle");
    assert.equal(check.state, expectedState);
    assert.equal(check.value, expectedValue);
    assert.equal(check.action.target, "config-chat_model");
    if (confirmed) assert.match(check.detail, /rejected by the provider/);
  }
}

console.log("January 2027 model selection and lifecycle warnings passed.");
