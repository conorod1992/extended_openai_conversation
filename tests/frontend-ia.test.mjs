import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {NAVIGATION, routeFromPath, searchSettings, shouldShowGlobalSettingsSearch} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {GUIDE_TOPICS, MEMORY_COMPARISON} from "../custom_components/extended_openai_conversation_responses/frontend/guide-content.js";
import {renderGuide} from "../custom_components/extended_openai_conversation_responses/frontend/guide-page.js";
import {knowledgeAvailabilityMarkup} from "../custom_components/extended_openai_conversation_responses/frontend/management-capabilities-ia.js";
import {MODEL_RESET_FIELDS} from "../custom_components/extended_openai_conversation_responses/frontend/management-memory-settings.js";
import {renderMemorySettings} from "../custom_components/extended_openai_conversation_responses/frontend/memory-settings-ui.js";
import {renderOverview} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page.js";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

assert.deepEqual(NAVIGATION.map((item) => item.label), [
  "Overview", "Guide", "Assistant", "Capabilities", "Data & Memory", "Usage & Maintenance",
]);
assert.deepEqual(NAVIGATION.find((item) => item.id === "assistant").sections.map((item) => item.label), [
  "Basics", "Model & responses", "Conversation", "Prompt & context", "Voice", "Speech",
]);
assert.deepEqual(NAVIGATION.find((item) => item.id === "capabilities").sections.map((item) => item.label), [
  "Home Assistant & local handling", "Web search & Skills", "Request Rules", "Functions", "Guest Mode",
]);
assert.equal(routeFromPath("/extended-openai/assistant/advanced").section, "basics");
assert.deepEqual(routeFromPath("/extended-openai/capabilities/web-skills"), {page:"capabilities", section:"web-skills", legacy:false});
const dataMemory = NAVIGATION.find((item) => item.id === "data-memory");
assert.equal(dataMemory.path, "/extended-openai/data-memory/memory-settings");
assert.deepEqual(dataMemory.sections.map((item) => item.label), [
  "Memory settings", "Memories", "Knowledge Library", "Conversation history",
]);
assert.ok(NAVIGATION.flatMap((item) => item.sections).every((item) => item.description));

const legacy = {
  configuration: ["assistant", "basics"],
  tools: ["capabilities", "functions"],
  guest: ["capabilities", "guest-mode"],
  memories: ["data-memory", "memories"],
  knowledge: ["data-memory", "knowledge"],
  conversations: ["data-memory", "conversations"],
  usage: ["usage-maintenance", "usage"],
  diagnostics: ["usage-maintenance", "diagnostics"],
};
for (const [oldRoute, expected] of Object.entries(legacy)) {
  const route = routeFromPath(`/extended-openai/${oldRoute}`);
  assert.deepEqual([route.page, route.section], expected);
  assert.equal(route.legacy, true);
}
assert.deepEqual(routeFromPath("/extended-openai/assistant/conversation"), {page:"assistant", section:"conversation", legacy:false});
assert.deepEqual(routeFromPath("/extended-openai/data-memory"), {page:"data-memory", section:"memory-settings", legacy:false});

for (const query of ["archive", "memory", "timeout", "model", "Guest Mode", "voice", "backup", "embeddings", "context", "tools functions", "local handling", "web search", "skills", "knowledge"]) {
  assert.ok(searchSettings(query).length, `global settings search should find ${query}`);
}
assert.equal(searchSettings("timeout")[0].section, "conversation");
assert.equal(searchSettings("backup")[0].section, "backup-restore");
for (const query of ["memory", "temporary", "embeddings", "shared household"]) {
  const result = searchSettings(query)[0];
  assert.equal(result?.page, "data-memory", `${query} should resolve to Data & Memory`);
  assert.equal(result?.section, "memory-settings", `${query} should resolve to Memory settings`);
  assert.equal(result?.target, "config-memory");
}
for (const [query, section] of [["local handling","home-assistant"],["web search","web-skills"],["skills","web-skills"]]) {
  const result = searchSettings(query)[0];
  assert.equal(result?.page, "capabilities", `${query} should resolve to Capabilities`);
  assert.equal(result?.section, section);
}
assert.equal(searchSettings("knowledge")[0].section, "knowledge");
for (const [page, section] of [["assistant", "basics"], ["capabilities", "home-assistant"], ["capabilities", "web-skills"], ["capabilities", "guest-mode"], ["data-memory", "memory-settings"], ["data-memory", "knowledge"], ["usage-maintenance", "retention"]]) {
  assert.equal(shouldShowGlobalSettingsSearch(page, section), true);
}
for (const [page, section] of [["guide", null], ["capabilities", "functions"], ["data-memory", "memories"], ["data-memory", "conversations"], ["usage-maintenance", "usage"]]) {
  assert.equal(shouldShowGlobalSettingsSearch(page, section), false);
}

const memoryPanel = {
  _configDirty: false,
  _e: escape,
  _result: {
    options: {
      memory_mode: [{value:"off",label:"Off"},{value:"manual",label:"Manual"},{value:"automatic",label:"Automatic"}],
      temporary_memory: [{value:"off",label:"Off"},{value:"balanced",label:"Balanced"},{value:"eager",label:"Eager"}],
      memory_retrieval_mode: [{value:"lexical",label:"Lexical"},{value:"hybrid",label:"Hybrid"}],
      shared_memory_mode: [{value:"disabled",label:"Disabled"},{value:"explicit",label:"Explicit"},{value:"automatic",label:"Automatic"}],
    },
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
const lexicalMemoryHtml = renderMemorySettings(memoryPanel);
for (const key of ["memory_mode", "temporary_memory", "memory_auto_retrieve_limit", "memory_retrieval_mode", "memory_embedding_model", "shared_memory_mode"]) {
  assert.match(lexicalMemoryHtml, new RegExp(`data-memory-config="${key}"`));
}
assert.match(lexicalMemoryHtml, /data-memory-config="memory_embedding_model"[^>]*disabled/);
assert.match(lexicalMemoryHtml, /Manage stored memories/);
memoryPanel._result.config.memory_retrieval_mode = "hybrid";
const hybridMemoryHtml = renderMemorySettings(memoryPanel);
assert.doesNotMatch(hybridMemoryHtml, /data-memory-config="memory_embedding_model"[^>]*disabled/);
assert.deepEqual(MODEL_RESET_FIELDS, ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"]);
assert.ok(MODEL_RESET_FIELDS.every((key) => !key.includes("memory")));

const knowledgePanel = {
  _data:{is_admin:true},
  _selectedAgent:()=>({feature_status:{knowledge:{state:"enabled"}}}),
};
const knowledgeAvailability = knowledgeAvailabilityMarkup(knowledgePanel);
assert.match(knowledgeAvailability, /Allow the assistant to use Knowledge/);
assert.match(knowledgeAvailability, /knowledge-enabled-toggle/);
assert.match(knowledgeAvailability, /checked/);
knowledgePanel._data.is_admin = false;
assert.equal(knowledgeAvailabilityMarkup(knowledgePanel), "");

assert.ok(GUIDE_TOPICS.length >= 12);
assert.ok(GUIDE_TOPICS.some((topic) => topic.id === "guest-mode"));
assert.ok(GUIDE_TOPICS.some((topic) => topic.id === "request-rules"));
assert.ok(GUIDE_TOPICS.every((topic) => topic.action?.page));
assert.deepEqual(MEMORY_COMPARISON.map((row) => row[0]), [
  "Conversation context", "Temporary memory", "Persistent memory", "Conversation archive", "Knowledge Library",
]);

const guidePanel = {_guideQuery:"guest", _guideTopic:"guest-mode", _e:escape, _empty:(message) => message};
const guideHtml = renderGuide(guidePanel);
assert.match(guideHtml, /Guest Mode/);
assert.doesNotMatch(guideHtml, /Choosing a model\/provider/);
assert.match(guideHtml, /Configure Guest Mode/);
assert.match(guideHtml, /class="guide-groups"/);
assert.match(guideHtml, /Privacy &amp; access/);
assert.match(guideHtml, /guide-quick-icon/);

const overviewAgent = {title:"Kitchen",provider:"OpenAI",model:"gpt-test",function_count:3,function_group_count:2,memory_mode:"hybrid",memory_count:4,knowledge_source_count:2,archive_enabled:true,guest_mode:{state:"active",has_home_assistant_exclusions:false}};
const overviewPanel = {
  _result:{usage:{today:{total_tokens:12},month:{total_tokens:34}},conversations:{archive_retention_days:30}},
  _e:escape,
  _titleCase:(value) => value,
};
const overviewHtml = renderOverview(overviewPanel, overviewAgent);
for (const label of ["Assistant", "Capabilities", "Memory &amp; Knowledge", "Conversation history", "Guest Mode", "Usage"]) assert.match(overviewHtml, new RegExp(label));
assert.match(overviewHtml, /Review recommended/);
assert.match(overviewHtml, /data-page="capabilities"/);
assert.match(overviewHtml, /dashboard-icon/);
assert.match(overviewHtml, /mdi:robot-outline/);
assert.match(overviewHtml, /dashboard-tone-warning/);

const partialOverviewPanel = {
  _result:{usage:{today:{total_tokens:1234},month:{total_tokens:5678}},conversations:{archive_retention_days:30},load_errors:[{key:"knowledge",label:"Knowledge",message:"offline"}]},
  _e:escape,
  _titleCase:(value) => value,
};
const partialOverviewHtml = renderOverview(partialOverviewPanel, {...overviewAgent, guest_mode:{state:"inactive",has_home_assistant_exclusions:true}});
assert.match(partialOverviewHtml, /Knowledge could not be loaded/);
assert.match(partialOverviewHtml, /1,234 tokens today/);
assert.match(partialOverviewHtml, /5,678 this month/);

const panel = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
const editor = (
  await Promise.all([
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js",
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js",
  ].map((path) => readFile(new URL(path, import.meta.url), "utf8")))
).join("\n");
const overview = (
  await Promise.all([
    "../custom_components/extended_openai_conversation_responses/frontend/overview-page.js",
    "../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js",
  ].map((path) => readFile(new URL(path, import.meta.url), "utf8")))
).join("\n");
const memoryManagement = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-memory-settings.js", import.meta.url), "utf8");
const capabilitiesIA = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-capabilities-ia.js", import.meta.url), "utf8");
const featureStatus = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-feature-status.js", import.meta.url), "utf8");
const bootstrap = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url), "utf8");
const homeAssistant = panel.slice(panel.indexOf("  _homeAssistant("), panel.indexOf("  _overview("));
assert.match(panel, /top-section-mobile/);
assert.match(panel, /id="local-section"/);
assert.doesNotMatch(panel, /local-section-mobile/);
assert.doesNotMatch(panel, /<aside class="local-nav"/);
assert.match(panel, /aria-current="page"/);
assert.match(panel, /settings-result/);
assert.match(panel, /class="chart-axis"/);
assert.match(panel, /chartDays/);
assert.match(panel, /font-size:14px;line-height:1\.45/);
assert.match(panel, /background:color-mix\(in srgb,var\(--secondary-background-color\) 42%,var\(--primary-background-color\)\)/);
assert.doesNotMatch(homeAssistant, /this\._metric\("Assistant", agent\.title\)/);
assert.doesNotMatch(homeAssistant, /this\._metric\("Provider", agent\.provider\)/);
assert.doesNotMatch(homeAssistant, /this\._metric\("Exposed context"/);
assert.match(homeAssistant, /Home Assistant's Assist exposure settings decide which entities may be used by the assistant/);
assert.match(homeAssistant, /Include exposed entity states in the prompt/);
assert.match(homeAssistant, /Turning this off does not necessarily prevent the assistant from using exposed entities/);
assert.match(homeAssistant, /Configure exposed entity context/);
assert.match(overview, /dashboard-action/);
assert.match(overview, /panel\._broadcastSelected = new Set/);
assert.match(overview, /available\.has\(entityId\)/);
assert.match(editor, /panel\._configSectionFilter/);
assert.match(editor, /if \(root\.querySelector\("#regex-rules"\)\)/);
assert.match(editor, /if \(voiceMappings\)/);
assert.doesNotMatch(editor, /id="config-search"/);
assert.match(editor, /class="agent-actions-menu"/);
assert.match(editor, /aria-haspopup="menu"/);
assert.ok(editor.indexOf("Duplicate agent") < editor.indexOf("Import configuration"));
assert.ok(editor.indexOf("Import configuration") < editor.indexOf("Export configuration"));
assert.match(editor, /const cleanOnly = panel\._configDirty/);
assert.match(editor, /id="duplicate-agent" \$\{cleanOnly\}/);
assert.match(editor, /id="export-agent" \$\{cleanOnly\}/);
assert.match(editor, /id="import-agent">Import configuration/);
assert.match(memoryManagement, /data-memory\/memory-settings/);
assert.match(memoryManagement, /assistant\/model-responses/);
assert.match(featureStatus, /subsection: "memory-settings", label: "Configure memory"/);
assert.doesNotMatch(featureStatus, /assistant.*advanced/);
assert.match(capabilitiesIA, /capabilities\/home-assistant/);
assert.match(capabilitiesIA, /capabilities\/web-skills/);
assert.match(capabilitiesIA, /return \["local"\]/);
assert.match(capabilitiesIA, /return \["capabilities"\]/);
assert.match(capabilitiesIA, /knowledge_enabled = desired/);
assert.match(capabilitiesIA, /configuration", "validate"/);
assert.match(bootstrap, /management-capabilities-ia\.js/);
