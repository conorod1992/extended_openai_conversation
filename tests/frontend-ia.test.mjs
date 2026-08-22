import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {NAVIGATION, routeFromPath, searchSettings} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
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
  "Home Assistant", "Functions", "Guest Mode",
]);

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

assert.ok(GUIDE_TOPICS.length >= 12);
assert.ok(GUIDE_TOPICS.some((topic) => topic.id === "guest-mode"));
assert.ok(GUIDE_TOPICS.every((topic) => topic.action?.page));
assert.deepEqual(MEMORY_COMPARISON.map((row) => row[0]), [
  "Conversation context", "Temporary memory", "Persistent memory", "Conversation archive", "Knowledge Library",
]);

const guidePanel = {_guideQuery:"guest", _guideTopic:"guest-mode", _e:escape, _empty:(message) => message};
const guideHtml = renderGuide(guidePanel);
assert.match(guideHtml, /Guest Mode/);
assert.doesNotMatch(guideHtml, /Choosing a model\/provider/);
assert.match(guideHtml, /Configure Guest Mode/);

const overviewPanel = {
  _result:{usage:{today:{total_tokens:12},month:{total_tokens:34}},conversations:{archive_retention_days:30}},
  _e:escape,
  _titleCase:(value) => value,
};
const overviewHtml = renderOverview(overviewPanel, {title:"Kitchen",provider:"OpenAI",model:"gpt-test",function_count:3,memory_mode:"hybrid",memory_count:4,knowledge_source_count:2,archive_enabled:true,guest_mode:{state:"active",has_home_assistant_exclusions:false}});
for (const label of ["Assistant", "Capabilities", "Memory &amp; Knowledge", "Conversation history", "Guest Mode", "Usage"]) assert.match(overviewHtml, new RegExp(label));
assert.match(overviewHtml, /Review recommended/);
assert.match(overviewHtml, /data-page="capabilities"/);

const panel = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
const editor = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
const overview = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page.js", import.meta.url), "utf8");
assert.match(panel, /top-section-mobile/);
assert.match(panel, /local-section-mobile/);
assert.match(panel, /aria-current="page"/);
assert.match(panel, /settings-result/);
assert.match(overview, /dashboard-action/);
assert.match(editor, /panel\._configSectionFilter/);
assert.match(editor, /if \(root\.querySelector\("#regex-rules"\)\)/);
assert.match(editor, /if \(voiceMappings\)/);
