import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {NAVIGATION, routeFromPath, searchSettings, shouldShowGlobalSettingsSearch} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {renderMemorySettings} from "../custom_components/extended_openai_conversation_responses/frontend/memory-settings-ui.js";
import {MODEL_RESET_FIELDS} from "../custom_components/extended_openai_conversation_responses/frontend/management-memory-settings.js";

const dataMemory = NAVIGATION.find((item) => item.id === "data-memory");
assert.equal(dataMemory.path, "/extended-openai/data-memory/memory-settings");
assert.deepEqual(dataMemory.sections.map((item) => item.label), [
  "Memory settings", "Memories", "Knowledge Library", "Conversation history",
]);
assert.deepEqual(routeFromPath("/extended-openai/data-memory"), {page: "data-memory", section: "memory-settings", legacy: false});
assert.equal(shouldShowGlobalSettingsSearch("data-memory", "memory-settings"), true);
for (const query of ["memory", "temporary", "embeddings", "shared household"]) {
  const result = searchSettings(query)[0];
  assert.equal(result?.page, "data-memory", `${query} should resolve to Data & Memory`);
  assert.equal(result?.section, "memory-settings", `${query} should resolve to Memory settings`);
  assert.equal(result?.target, "config-memory");
}

const options = {
  memory_mode: [
    {value: "off", label: "Off"},
    {value: "manual", label: "Manual"},
    {value: "automatic", label: "Automatic"},
  ],
  temporary_memory: [
    {value: "off", label: "Off"},
    {value: "balanced", label: "Balanced"},
    {value: "eager", label: "Eager"},
  ],
  memory_retrieval_mode: [
    {value: "lexical", label: "Lexical"},
    {value: "hybrid", label: "Hybrid"},
  ],
  shared_memory_mode: [
    {value: "disabled", label: "Disabled"},
    {value: "explicit", label: "Explicit"},
    {value: "automatic", label: "Automatic"},
  ],
};
const panel = {
  _configDirty: false,
  _e: (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
  _result: {
    options,
    config: {
      memory_mode: "manual",
      temporary_memory: "balanced",
      memory_auto_retrieve_limit: 3,
      memory_retrieval_mode: "lexical",
      memory_embedding_model: "text-embedding-3-small",
      shared_memory_mode: "explicit",
    },
  },
};

const lexical = renderMemorySettings(panel);
for (const key of ["memory_mode", "temporary_memory", "memory_auto_retrieve_limit", "memory_retrieval_mode", "memory_embedding_model", "shared_memory_mode"]) {
  assert.match(lexical, new RegExp(`data-memory-config="${key}"`));
}
assert.match(lexical, /data-memory-hybrid><div class="setting"/);
assert.match(lexical, /data-memory-config="memory_embedding_model"[^>]*disabled/);
assert.match(lexical, /Manage stored memories/);

panel._result.config.memory_retrieval_mode = "hybrid";
const hybrid = renderMemorySettings(panel);
assert.match(hybrid, /class="dependent " data-memory-hybrid/);
assert.doesNotMatch(hybrid, /data-memory-config="memory_embedding_model"[^>]*disabled/);

assert.deepEqual(MODEL_RESET_FIELDS, ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"]);
assert.ok(MODEL_RESET_FIELDS.every((key) => !key.includes("memory")));

const managementPatch = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-memory-settings.js", import.meta.url), "utf8");
for (const key of ["memory_mode", "temporary_memory", "memory_auto_retrieve_limit", "memory_retrieval_mode", "memory_embedding_model", "shared_memory_mode"]) {
  assert.match(managementPatch, new RegExp(`"${key}"`));
}
assert.match(managementPatch, /data-memory\/memory-settings/);
assert.match(managementPatch, /assistant\/model-responses/);

const featureStatus = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-feature-status.js", import.meta.url), "utf8");
assert.match(featureStatus, /subsection: "memory-settings", label: "Configure memory"/);
