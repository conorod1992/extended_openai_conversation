import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const guideSource = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/guide-page-impl.js", import.meta.url),
  "utf8",
);

assert.match(guideSource, /const GUIDE_TOPIC_SEARCH = new Map/);
assert.match(guideSource, /GUIDE_TOPIC_SEARCH\.get\(topic\.id\)\?\.includes\(query\)/);
assert.match(guideSource, /applyGuideSearch\(panel, panel\._guideQuery\)/);
assert.doesNotMatch(
  guideSource.match(/export function bindGuide\(panel\) \{[\s\S]*$/)?.[0] || "",
  /panel\._render\(\)/,
  "Guide typing must not rerender the route",
);
assert.doesNotMatch(guideSource, /querySelectorAll\("\.guide-topic\[open\]"\)/);
assert.match(guideSource, /panel\._openGuideTopicElement/);
assert.doesNotMatch(guideSource, /return `<style>/);

const guideContentSource = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/guide-content.js", import.meta.url),
  "utf8",
);
const requestRulesStart = guideContentSource.indexOf('id: "request-rules"');
const requestRulesEnd = guideContentSource.indexOf("\n  {\n    id:", requestRulesStart + 1);
const requestRulesGuide = guideContentSource.slice(requestRulesStart, requestRulesEnd);
assert.match(requestRulesGuide, /ExtendedOpenAI sentence patterns/);
assert.match(requestRulesGuide, /Continue matching after this rule/);
assert.match(requestRulesGuide, /Captured value/);
assert.match(requestRulesGuide, /Rule Sharing/);
assert.match(requestRulesGuide, /Set conversation response overrides the generic success response/);
assert.match(requestRulesGuide, /Continue to AI runs only after normal completion/);
assert.match(requestRulesGuide, /response_variable stay within the action sequence/);
assert.match(requestRulesGuide, /Newly created rules start enabled/);
assert.match(requestRulesGuide, /groups only help organise and filter rules/i);
assert.doesNotMatch(requestRulesGuide, /Home Assistant sentence patterns|Hassil sentence format/);


const {GUIDE_TOPICS} = await import("../custom_components/extended_openai_conversation_responses/frontend/guide-content.js");
const backupPrivacyTopics = GUIDE_TOPICS.filter(topic => /privacy|backup/.test(topic.id));
assert.equal(backupPrivacyTopics.length, 2);
for (const topic of backupPrivacyTopics) {
  const text = topic.body.map(block => block.text || "").join(" ");
  assert.match(text, /include provider (?:connection settings and API |API )credentials/i);
  assert.match(text, /not encrypted/);
  assert.doesNotMatch(text, /credentials.*(?:are excluded|are not included)/);
}


const configurationGuide = await readFile(new URL("../docs/configuration.md", import.meta.url), "utf8");
const backupSection = configurationGuide.split("## Backup & Restore")[1].split("## Speech cleanup")[0];
assert.match(backupSection, /preserve inline credentials and include the parent provider connection/);
assert.match(backupSection, /when configuration is selected/);
assert.match(backupSection, /not encrypted/);
assert.match(backupSection, /explicit shared-connection choice/);
assert.doesNotMatch(backupSection, /Provider API keys, OAuth tokens, parent config-entry credentials.*are excluded/);
assert.doesNotMatch(backupSection, /Re-enter provider credentials separately|Reconnect or re-enter provider credentials separately/);
