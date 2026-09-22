import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const guideSource = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/guide-page-impl.js", import.meta.url),
  "utf8",
);
const managementCss = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management.css", import.meta.url),
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
assert.match(managementCss, /\/\* Guide route \*\//);
assert.match(managementCss, /\.guide-topic\[open\]/);
assert.match(managementCss, /\.guide-quick-start/);
