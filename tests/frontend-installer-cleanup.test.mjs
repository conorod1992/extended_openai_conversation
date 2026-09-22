import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) =>
  new URL(`../custom_components/extended_openai_conversation_responses/frontend/${name}`, import.meta.url);

const panel = await readFile(frontend("management-panel.js"), "utf8");
const modules = await Promise.all([
  "agent-config-editor.js",
  "management-navigation-search.js",
  "management-setting-metadata.js",
  "management-draft-navigation.js",
  "management-configuration-guidance.js",
  "management-decision-guidance.js",
  "management-overview-health-clarity.js",
].map(async (name) => [name, await readFile(frontend(name), "utf8")]));

for (const [name, source] of modules) {
  assert.doesNotMatch(source, /installManagement[A-Za-z]+/u, `${name} should not export a runtime installer`);
  assert.doesNotMatch(source, /prototype\.(?:_renderContent|_confirm|_clearConfigDraft|_configurationDirtyDestinations)/u,
    `${name} should not replace panel methods at runtime`);
  assert.doesNotMatch(source, /Symbol\.for\("extended-openai\.management-/u,
    `${name} should not retain installer sentinel state`);
}

assert.doesNotMatch(panel, /management-bootstrap\.js|initializeManagementPanel/u);
assert.match(panel, /_configurationDirtyDestinations\(\)\s*\{\s*return configurationDestinations\(this\);/u);
assert.match(panel, /_settingsSearchConfig = null;/u);
assert.match(panel, /enhanceConfirmationScope\(this, subject\)/u);

assert.match(
  panel,
  /connectedCallback\(\)[\s\S]*bindConfigurationClarity\(this\)/u,
  "persistent configuration clarity binding belongs to connection lifecycle",
);
const orderedCalls = [
  "navigationSearchModule?.enhanceNavigationSearch(this)",
  "enhanceConfigurationClarity(this)",
  "enhanceConfigurationGuidance(this)",
  "enhanceOverviewHealthClarity(this)",
];
let previous = -1;
for (const call of orderedCalls) {
  const index = panel.indexOf(call);
  assert.ok(index > previous, `${call} should retain explicit per-render composition order`);
  previous = index;
}

assert.doesNotMatch(panel, /polishRenderedCopy/u);
assert.match(panel, /queueMicrotask\(\(\) => enhanceOverviewHealthClarity\(this\)\)/u);

assert.doesNotMatch(panel, /bindConfigurationGuidance/, "configuration inputs must own guidance invalidation without another event listener layer");

assert.doesNotMatch(panel, /applyManagementToolbarLayout|polishSettingsLayout/u,
  "deterministic toolbar/settings layout belongs to the canonical renderer");
assert.match(panel, /settingsSearchShellMarkup\(this\)/u);
assert.match(panel, /import\("\.\/management-navigation-search\.js"\)/u);
assert.match(panel, /data-eoc-persistent-shell/u);
assert.match(panel, /data-eoc-main/u);
