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
assert.match(requestRulesGuide, /continue to AI using the current model/);
assert.match(requestRulesGuide, /Captured value/);
assert.match(requestRulesGuide, /Backup & Restore/);
assert.match(requestRulesGuide, /Set conversation response overrides the generic success response/);
assert.match(requestRulesGuide, /Continue to AI runs only after normal completion/);
assert.match(requestRulesGuide, /response_variable stay within the action sequence/);
assert.match(requestRulesGuide, /Set conversation response/);
assert.match(requestRulesGuide, /Home Assistant response variable such as battery/);
assert.match(requestRulesGuide, /repair message before its actions, a lower-priority rule, or an AI handoff/);
assert.doesNotMatch(requestRulesGuide, /alias when later steps/);
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


const broadcastGuide = GUIDE_TOPICS.find(topic => topic.id === "broadcast");
const broadcastText = broadcastGuide.body.map(block => [block.title || "", block.text || (block.items || []).join(" ")].join(" ")).join(" ");
assert.match(broadcastText, /Broadcast dinner is ready/);
assert.match(broadcastText, /Built-in voice command/);
assert.match(broadcastText, /tell the kitchen \{message\}/);
assert.match(broadcastText, /6\.8\.3/);
assert.match(broadcastText, /upstream/);
assert.match(broadcastText, /CONTROL/);
assert.doesNotMatch(broadcastText, /clear targeted wording can be resolved without/);
const broadcastCode = broadcastGuide.body.find(block => block.type === "code").text;
assert.equal(broadcastCode, '- action: extended_openai_conversation_responses.call_function\n  data:\n    function: send_broadcast\n    arguments:\n      destination: kitchen\n      message: "{{ message }}"');

const {bindGuide, renderGuide} = await import("../custom_components/extended_openai_conversation_responses/frontend/guide-page-impl.js");
const escape = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const guideMarkup = renderGuide({_e:escape});
assert.match(guideMarkup, /href="https:\/\/conorod1992\.github\.io\/extended_openai_conversation\/features\/quiet-hours\/#legacy-ownership-recovery" target="_blank" rel="noopener noreferrer">Legacy ownership recovery<\/a>/);

// Exercise the public binder and its real input/click handlers without replacing
// the route DOM. The small nodes retain identity, visibility and accordion state.
const scrolls = [];
function node(properties = {}) {
  const listeners = new Map();
  return {
    hidden: false,
    open: false,
    ...properties,
    addEventListener(type, listener) {
      listeners.set(type, [...(listeners.get(type) || []), listener]);
    },
    dispatch(type) {
      for (const listener of listeners.get(type) || []) listener({target:this});
    },
    scrollIntoView() { scrolls.push(this.id); },
  };
}
const search = node({value:""});
const topics = new Map(GUIDE_TOPICS.map(topic => [topic.id, node({id:`guide-${topic.id}`})]));
const groups = new Map([...guideMarkup.matchAll(/data-guide-group="([^"]+)"/g)].map(([, id]) => {
  const description = {textContent:""};
  return [id, node({dataset:{guideGroup:id}, querySelector:() => description})];
}));
const quickTasks = [...guideMarkup.matchAll(/data-guide-topic="([^"]+)"/g)].map(([, topic]) => node({dataset:{guideTopic:topic}}));
const noResults = node();
const host = {insertBefore() {}};
const root = {
  querySelector(selector) {
    if (selector === "#guide-search") return search;
    if (selector === ".guide-groups") return host;
    if (selector === ".guide-no-results") return noResults;
    if (selector === ".guide-topic[open]") return [...topics.values()].find(topic => topic.open) || null;
    if (selector.startsWith("#guide-")) return topics.get(selector.slice(7)) || null;
    const group = selector.match(/^\[data-guide-group="([^"]+)"\]$/)?.[1];
    return groups.get(group) || null;
  },
  querySelectorAll(selector) {
    if (selector === ".guide-topic") return [...topics.values()];
    if (selector === ".guide-group") return [...groups.values()];
    if (selector === "[data-guide-topic]") return quickTasks;
    return [];
  },
};
const panel = {
  shadowRoot:root,
  _guideQuery:"",
  _render() { assert.fail("Guide filtering and quick-task navigation must preserve the route DOM"); },
};
const originalCSS = globalThis.CSS;
globalThis.CSS = {escape:String};
try {
  bindGuide(panel);
  function filter(query) {
    search.value = query;
    search.dispatch("input");
    assert.equal(panel._guideQuery, query);
  }
  for (const topic of GUIDE_TOPICS) {
    filter(topic.title.toUpperCase());
    assert.equal(topics.get(topic.id).hidden, false, `The displayed title must find ${topic.id}`);
  }
  for (const [query, id] of [["shareable setup", "backup-restore"], ["captures", "request-debugging"], ["capture limit", "request-debugging"]]) {
    filter(query);
    assert.equal(topics.get(id).hidden, false, `Visible topic content must be searchable: ${query}`);
  }

  const previous = topics.get("persistent-memory");
  const destination = topics.get("broadcast");
  previous.open = true;
  previous.dispatch("toggle");
  filter("memory");
  assert.equal(destination.hidden, true);
  quickTasks.find(button => button.dataset.guideTopic === "broadcast").dispatch("click");
  assert.equal(panel._guideQuery, "");
  assert.equal(search.value, "");
  assert.equal(noResults.hidden, true);
  assert.ok([...topics.values(), ...groups.values()].every(item => !item.hidden));
  assert.equal(previous.open, false);
  assert.equal(destination.open, true);
  assert.equal(panel._guideTopic, "broadcast");
  assert.equal(panel._openGuideTopicElement, destination);
  assert.deepEqual(scrolls, ["guide-broadcast"]);

  filter("zzzz-no-guide-topic");
  assert.equal(noResults.hidden, false);
  quickTasks.find(button => button.dataset.guideTopic === "knowledge").dispatch("click");
  assert.equal(noResults.hidden, true);
  assert.equal(search.value, "");
  assert.equal(panel._guideQuery, "");
  assert.equal(destination.open, false);
  assert.equal(topics.get("knowledge").open, true);
  assert.deepEqual(scrolls, ["guide-broadcast", "guide-knowledge"]);
} finally {
  if (originalCSS === undefined) delete globalThis.CSS;
  else globalThis.CSS = originalCSS;
}
