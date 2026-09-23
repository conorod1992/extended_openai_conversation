import assert from "node:assert/strict";
import {
  dirtyConfigurationDestinations,
  dirtyConfigurationKeys,
  friendlySettingLabel,
  friendlySettingValue,
  settingEffectBadges,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-setting-metadata.js";
import {
  buildSettingsSearchProjection,
  searchProjectedSettings,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";
import {SETTINGS_INDEX} from "../custom_components/extended_openai_conversation_responses/frontend/management-settings-index.js";
import {CONFIG_OWNER_BY_KEY} from "../custom_components/extended_openai_conversation_responses/frontend/management-config-owners.js";

assert.equal(friendlySettingLabel("memory_retrieval_mode"), "Relevance matching");
assert.equal(friendlySettingLabel("temperature"), "Response creativity (temperature)");
assert.equal(friendlySettingValue("memory_retrieval_mode", "lexical"), "Keyword matching (Lexical)");
assert.equal(friendlySettingValue("memory_retrieval_mode", "hybrid"), "Semantic + keyword matching (Hybrid)");
assert.equal(friendlySettingValue("api_mode", "responses"), "Responses API");
assert.deepEqual(settingEffectBadges("memory_retrieval_mode", "hybrid"), ["Requires embeddings"]);
assert.deepEqual(settingEffectBadges("memory_retrieval_mode", "lexical"), ["No embedding request"]);
assert.deepEqual(settingEffectBadges("local_intents_enabled", true), ["No AI call when matched"]);
assert.deepEqual(settingEffectBadges("archive_enabled", true), []);
assert.deepEqual(settingEffectBadges("temperature", 0.8), []);
assert.deepEqual(settingEffectBadges("local_intents_enabled", true, {disabled:true}), []);

for (const {configKey, page, section} of SETTINGS_INDEX) {
  if (configKey) assert.equal(CONFIG_OWNER_BY_KEY[configKey], `${page}/${section}`);
}

const projection = buildSettingsSearchProjection(SETTINGS_INDEX);
assert.equal(
  searchProjectedSettings("lexical", projection).some((item) => item.configKey === "memory_retrieval_mode"),
  true,
  "technical retrieval terms must remain searchable",
);
assert.equal(
  searchProjectedSettings("service tier", projection).some((item) => item.configKey === "service_tier"),
  true,
  "technical Service Tier terminology must remain searchable",
);
assert.equal(
  searchProjectedSettings("hassil").some((item) => item.configKey === "local_intents_enabled"),
  true,
  "Hassil should remain a searchable alias for local handling",
);

const baseline = {
  chat_model: "gpt-5-mini",
  temperature: 1,
  memory_retrieval_mode: "lexical",
  archive_enabled: false,
};
const panel = {
  _agentId: "agent-a",
  _draftAgentId: "agent-a",
  _configData: {title:"Kitchen Assistant", config: structuredClone(baseline)},
  _draft: structuredClone(baseline),
  _draftTitle: "Kitchen Assistant",
  _configDirty: false,
  _guestDirty: false,
  _page: "assistant",
  _subsection: "basics",
};
assert.deepEqual([...dirtyConfigurationKeys(panel)], []);
assert.deepEqual([...dirtyConfigurationDestinations(panel)], []);

panel._draft.chat_model = "gpt-5.6";
panel._draft.memory_retrieval_mode = "hybrid";
panel._draft.archive_enabled = true;
panel._draftTitle = "Jarvis";
panel._configDirty = true;
assert.deepEqual(
  new Set(dirtyConfigurationKeys(panel)),
  new Set(["chat_model", "memory_retrieval_mode", "archive_enabled", "__title"]),
);
assert.deepEqual(
  dirtyConfigurationDestinations(panel),
  new Set(["assistant/basics", "data-memory/memory-settings", "data-memory/conversations"]),
);

panel._unsavedState = {destinations: () => new Set(["capabilities/guest-mode"])};
assert.equal(dirtyConfigurationDestinations(panel).has("capabilities/guest-mode"), true);

panel._draft = structuredClone(baseline);
panel._draftTitle = "Kitchen Assistant";
panel._unsavedState = {destinations: () => new Set()};
assert.deepEqual([...dirtyConfigurationDestinations(panel)], []);
