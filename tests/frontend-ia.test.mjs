import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {NAVIGATION, routeFromPath, searchSettings, shouldShowGlobalSettingsSearch} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {GUIDE_TOPICS, MEMORY_COMPARISON} from "../custom_components/extended_openai_conversation_responses/frontend/guide-content.js";
import {renderGuide} from "../custom_components/extended_openai_conversation_responses/frontend/guide-page.js";
import {renderOverview} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page.js";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

assert.deepEqual(NAVIGATION.map((item) => item.label), [
  "Overview", "Guide", "Assistant", "Capabilities", "Data & Memory", "Usage & Maintenance",
]);
assert.deepEqual(NAVIGATION.find((item) => item.id === "assistant").sections.map((item) => item.label), [
  "Basics", "Model & responses", "Conversation", "Prompt & context", "Voice", "Speech", "Advanced",
]);
assert.deepEqual(NAVIGATION.find((item) => item.id === "capabilities").sections.map((item) => item.label), [
  "Home Assistant", "Request Rules", "Functions", "Guest Mode",
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

for (const query of ["archive", "memory", "timeout", "model", "Guest Mode", "voice", "backup", "embeddings", "context", "tools functions"]) {
  assert.ok(searchSettings(query).length, `global settings search should find ${query}`);
}
assert.equal(searchSettings("timeout")[0].section, "conversation");
assert.equal(searchSettings("backup")[0].section, "backup-restore");
for (const [page, section] of [["assistant", "basics"], ["capabilities", "guest-mode"], ["usage-maintenance", "retention"]]) {
  assert.equal(shouldShowGlobalSettingsSearch(page, section), true);
}
for (const [page, section] of [["guide", null], ["capabilities", "functions"], ["data-memory", "memories"], ["data-memory", "knowledge"], ["data-memory", "conversations"], ["usage-maintenance", "usage"]]) {
  assert.equal(shouldShowGlobalSettingsSearch(page, section), false);
}

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
const overview = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page.js", import.meta.url), "utf8");
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
