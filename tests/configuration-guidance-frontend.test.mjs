import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  apiModeConsequence,
  dependencyGuidance,
  hybridMemoryGuidance,
  modelParameterGuidance,
  webSearchConflict,
  webSearchDetailConsequence,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-configuration-guidance.js";

assert.match(
  dependencyGuidance("conversation_continuity", {conversation_continuity: "ha_default"}).text,
  /Home Assistant is managing conversation sessions/,
);
assert.match(
  dependencyGuidance("archive_enabled", {archive_enabled: false}).text,
  /retained but have no effect/,
);
assert.equal(
  dependencyGuidance("local_intents_enabled", {local_intents_enabled: false}, {supported: false}),
  null,
  "the existing unsupported-Home-Assistant notice should remain authoritative",
);
assert.match(
  dependencyGuidance("local_intents_enabled", {local_intents_enabled: false}, {supported: true}).text,
  /local handling is off/,
);

const lexical = hybridMemoryGuidance({memory_retrieval_mode: "lexical"});
assert.equal(lexical.kind, "dormant");
assert.match(lexical.text, /saved model is retained/);
const hybrid = hybridMemoryGuidance({memory_retrieval_mode: "hybrid"});
assert.match(hybrid.text, /falls back to keyword matching/);

assert.deepEqual(
  apiModeConsequence(
    {api_mode: "auto"},
    {effective_api_mode: "responses"},
  ),
  {
    kind: "info",
    title: "Automatic format resolved",
    text: "Auto currently resolves to Responses API for this model.",
  },
);
assert.equal(apiModeConsequence({api_mode: "responses"}, {effective_api_mode: "responses"}), null);

const apiConflict = webSearchConflict(
  {web_search: true},
  {
    web_search: {
      available: false,
      reason: "requires_responses",
      message: "Web Search requires the Responses API.",
    },
  },
);
assert.equal(apiConflict.kind, "warning");
assert.equal(apiConflict.action.target, "config-api_mode");
assert.match(apiConflict.title, /API format/);

const providerConflict = webSearchConflict(
  {web_search: true},
  {
    web_search: {
      available: false,
      reason: "direct_openai_only",
      message: "Hosted search requires direct OpenAI.",
    },
  },
);
assert.equal(providerConflict.kind, "warning");
assert.equal(providerConflict.action, undefined);
assert.equal(webSearchConflict({web_search: false}, {web_search: {available: false}}), null);

assert.match(
  webSearchDetailConsequence(
    {web_search: true, web_search_context: "high"},
    {web_search: {available: true}},
  ).text,
  /increase the amount of context/,
);
assert.equal(
  webSearchDetailConsequence(
    {web_search: true, web_search_context: "low"},
    {web_search: {available: true}},
  ),
  null,
);

const unsupportedTemperature = modelParameterGuidance("temperature", {supports_temperature: false});
assert.match(unsupportedTemperature.title, /Not used/);
assert.match(unsupportedTemperature.text, /saved value is retained/);
assert.equal(modelParameterGuidance("temperature", {supports_temperature: true}), null);

const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
const clarityIndex = bootstrap.lastIndexOf('await import("./management-configuration-clarity.js")');
const guidanceIndex = bootstrap.lastIndexOf('await import("./management-configuration-guidance.js")');
assert.ok(clarityIndex >= 0 && guidanceIndex > clarityIndex, "guidance must install after clarity");

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-configuration-guidance.js", import.meta.url),
  "utf8",
);
assert.match(source, /dataset\.eocInjectedModel/);
assert.match(source, /parent\.disabled = !config\.local_intents_enabled/);
assert.match(source, /configuration.*validate/s);
