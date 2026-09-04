import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  dirtyConfigurationDestinations,
  dirtyConfigurationKeys,
  friendlySettingLabel,
  friendlySettingValue,
  settingEffectBadges,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-configuration-clarity.js";
import {
  buildSettingsSearchProjection,
  searchProjectedSettings,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";
import {SETTINGS_INDEX} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";

const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
assert.match(bootstrap, /management-configuration-clarity\.js/);
assert.ok(
  bootstrap.indexOf('await import("./debug-management.js")')
    < bootstrap.indexOf('await import("./management-configuration-clarity.js")'),
  "configuration clarity should install after the other management extensions",
);

assert.equal(friendlySettingLabel("memory_retrieval_mode"), "Relevance matching");
assert.equal(friendlySettingLabel("temperature"), "Response creativity (temperature)");
assert.equal(friendlySettingValue("memory_retrieval_mode", "lexical"), "Keyword matching (Lexical)");
assert.equal(friendlySettingValue("memory_retrieval_mode", "hybrid"), "Semantic + keyword matching (Hybrid)");
assert.equal(friendlySettingValue("api_mode", "responses"), "Responses API");
assert.deepEqual(settingEffectBadges("memory_retrieval_mode", "hybrid"), ["Requires embeddings"]);
assert.deepEqual(settingEffectBadges("memory_retrieval_mode", "lexical"), ["No embedding request"]);
assert.deepEqual(settingEffectBadges("local_intents_enabled", true), ["No AI call when matched"]);
assert.deepEqual(settingEffectBadges("archive_enabled", true), ["Stores data"]);
assert.deepEqual(settingEffectBadges("temperature", 0.8), ["Advanced"]);
assert.deepEqual(settingEffectBadges("local_intents_enabled", true, {disabled:true}), []);

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
  searchProjectedSettings("hassil", projection).some((item) => item.configKey === "local_intents_enabled"),
  true,
  "technical/local-handling terminology must remain searchable",
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

panel._guestDirty = true;
assert.equal(dirtyConfigurationDestinations(panel).has("capabilities/guest-mode"), true);

panel._draft = structuredClone(baseline);
panel._draftTitle = "Kitchen Assistant";
panel._guestDirty = false;
assert.deepEqual([...dirtyConfigurationDestinations(panel)], []);
