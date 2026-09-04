import assert from "node:assert/strict";

import {dirtyConfigurationKeys} from "../custom_components/extended_openai_conversation_responses/frontend/management-configuration-clarity.js";
import {
  configKeyForControl,
  configKeysForButton,
  rebuildConfigDirtyKeys,
  syncConfigDirtyKeys,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";

function panelFor(config = {temperature: 1, top_p: 1}) {
  return {
    _agentId: "agent-a",
    _draftAgentId: "agent-a",
    _configData: {title: "Jarvis", config: structuredClone(config)},
    _draft: structuredClone(config),
    _draftTitle: "Jarvis",
    _configDirty: false,
    _eocDirtyConfigKeys: new Set(),
  };
}

{
  const panel = panelFor();
  panel._draft.temperature = 2;
  let changed = syncConfigDirtyKeys(panel, ["temperature"]);
  panel._configDirty = changed.size > 0;
  assert.deepEqual([...changed], ["temperature"]);
  assert.equal(panel._configDirty, true);

  panel._draft.temperature = 1;
  changed = syncConfigDirtyKeys(panel, ["temperature"]);
  panel._configDirty = changed.size > 0;
  assert.deepEqual([...changed], []);
  assert.equal(panel._configDirty, false, "reverting the last field must clear dirty state");
}

{
  const panel = panelFor();
  panel._draft.temperature = 2;
  panel._draft.top_p = 0.5;
  syncConfigDirtyKeys(panel, ["temperature", "top_p"]);
  panel._draft.temperature = 1;
  const changed = syncConfigDirtyKeys(panel, ["temperature"]);
  assert.deepEqual([...changed], ["top_p"]);
  assert.equal(changed.size > 0, true, "reverting one field must keep another dirty field authoritative");
}

{
  const panel = panelFor();
  panel._draftTitle = "Kitchen";
  assert.deepEqual([...syncConfigDirtyKeys(panel, ["__title"])], ["__title"]);
  panel._draftTitle = "Jarvis";
  assert.deepEqual([...syncConfigDirtyKeys(panel, ["__title"])], []);
}

{
  const untouchedHeavyValue = {
    toJSON() {
      throw new Error("untouched heavyweight config was serialized");
    },
  };
  const panel = panelFor({temperature: 1});
  panel._configData.config.functions = untouchedHeavyValue;
  panel._draft.functions = untouchedHeavyValue;
  panel._draft.temperature = 2;

  const changed = syncConfigDirtyKeys(panel, ["temperature"]);
  assert.deepEqual([...changed], ["temperature"]);
  assert.deepEqual(
    [...dirtyConfigurationKeys(panel)],
    ["temperature"],
    "clarity indicators should consume the authoritative set without rescanning config",
  );
}

{
  const panel = panelFor({temperature: 1, functions: [{name: "one"}]});
  panel._draft.functions = [{name: "two"}];
  panel._draftTitle = "Kitchen";
  assert.deepEqual(
    new Set(rebuildConfigDirtyKeys(panel)),
    new Set(["functions", "__title"]),
    "non-hot synchronization must still rebuild complete dirty state when required",
  );
}

{
  assert.equal(configKeyForControl({dataset: {config: "temperature"}}), "temperature");
  assert.equal(configKeyForControl({dataset: {memoryConfig: "memory_mode"}}), "memory_mode");
  assert.equal(configKeyForControl({id: "voice-mappings", dataset: {}}), "voice_device_mappings");
  assert.equal(
    configKeyForControl({dataset: {}, matches: (selector) => selector === "[data-local-intent-exclusion]"}),
    "local_intent_exclusions",
  );
  assert.equal(
    configKeyForControl({dataset: {}, matches: (selector) => selector === ".regex-pattern,.regex-replacement"}),
    "speech_regex_replacements",
  );
  assert.equal(
    configKeyForControl({id: "conversation-timeout-preset", dataset: {}}),
    "conversation_timeout_minutes",
  );
}

function button({id = "", classes = [], dataset = {}} = {}) {
  return {
    id,
    dataset,
    classList: {contains: (name) => classes.includes(name)},
  };
}

{
  assert.deepEqual(
    configKeysForButton(button({id: "reset-prompt"})),
    [
      "prompt",
      "current_datetime_enabled",
      "exposed_entities_enabled",
      "current_datetime_template",
      "exposed_entities_template",
    ],
  );
  assert.deepEqual(
    configKeysForButton(button({id: "reset-model-parameters"})),
    ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"],
  );
  assert.deepEqual(
    configKeysForButton(button({classes: ["reset-context-template"], dataset: {templateKey: "exposed_entities_template"}})),
    ["exposed_entities_template"],
  );
  assert.deepEqual(
    configKeysForButton(button({classes: ["delete-regex"]})),
    ["speech_regex_replacements"],
  );
}
