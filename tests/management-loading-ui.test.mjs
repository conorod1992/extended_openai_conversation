import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [loading, overview, guide, debug, agentEditor, agentLoader, agentNativeYaml, requestRules, requestRulesLoader, bootstrap, routes] = await Promise.all([
  readFile(frontend("management-actions.js"), "utf8"),
  readFile(frontend("overview-page.js"), "utf8"),
  readFile(frontend("guide-page.js"), "utf8"),
  readFile(frontend("debug-management.js"), "utf8"),
  readFile(frontend("agent-config-editor.js"), "utf8"),
  readFile(frontend("agent-config-loader.js"), "utf8"),
  readFile(frontend("agent-config-native-yaml.js"), "utf8"),
  readFile(frontend("request-rules-ui.js"), "utf8"),
  readFile(frontend("request-rules-loader.js"), "utf8"),
  readFile(frontend("management-bootstrap.js"), "utf8"),
  readFile(frontend("management-route.js"), "utf8"),
]);

const panelSource = await readFile(frontend("management-panel.js"), "utf8");
assert.match(panelSource, /_call\("overview", "summary"\)/);
assert.match(loading, /_call\("configuration", "save"/);
assert.doesNotMatch(loading, /_loadAgents\(panel\._agentId\)/);
assert.match(panelSource, /Date.now\(\) - loadedAt > 30_000/);
assert.match(loading, /event\.stopImmediatePropagation\(\)/);
assert.match(routes, /panel\._viewKey\(\) === view && panel\._eocViewAssetToken === assetToken/);
assert.match(loading, /Document changed\. Validate & preview again before importing\./);
assert.match(loading, /validatedImportMatches\(panel\._importDocument, current\)/);
assert.match(panelSource, /section === "guest_mode" && action === "update"/);
assert.match(loading, /button\.id === "guest-policy-save"/);
assert.match(loading, /button\.classList\.contains\("rule-duplicate"\)/);
assert.match(loading, /button\.classList\.contains\("rule-delete"\)/);
assert.match(loading, /input\?\.classList\?\.contains\("rule-enabled"\)/);
assert.match(panelSource, /this\._eocRuleSavePromise/);

assert.match(overview, /import\("\.\/overview-page-impl\.js"\)/);
assert.doesNotMatch(overview, /from "\.\/overview-page-impl\.js"/);
assert.match(guide, /import\("\.\/guide-page-impl\.js"\)/);
assert.doesNotMatch(guide, /management-loading-performance\.js/);
assert.doesNotMatch(guide, /from "\.\/guide-page-base\.js"/);
assert.match(debug, /import\("\.\/debug-panel\.js"\)/);
assert.doesNotMatch(debug, /^import "\.\/debug-panel\.js"/m);

assert.match(agentEditor, /import "\.\/management-bootstrap\.js"/);
assert.doesNotMatch(agentEditor, /from "\.\/agent-config-editor-base\.js"/);
assert.doesNotMatch(agentEditor, /export \* from "\.\/agent-config-editor-base\.js"/);
assert.match(agentLoader, /import\("\.\/agent-config-native-yaml\.js"\)/);
assert.doesNotMatch(agentLoader, /agent-config-editor-model-v2\.js/);
assert.match(agentNativeYaml, /import \* as base from "\.\/agent-config-editor-model-v2\.js"/);
assert.match(agentNativeYaml, /export \* from "\.\/agent-config-editor-model-v2\.js"/);
assert.doesNotMatch(requestRules, /from "\.\/request-rules-ui-impl\.js"/);
assert.match(requestRulesLoader, /import\("\.\/request-rules-ui-impl\.js"\)/);
assert.match(routes, /event\.stopImmediatePropagation\(\)/);
assert.doesNotMatch(routes, /panel\._render\(\);\s*\/\/.*rule-search/);

const overviewModule = await import(frontend("overview-page.js"));
const guideModule = await import(frontend("guide-page.js"));
const loadingModule = await import(frontend("management-actions.js"));
const routeModule = await import(frontend("management-route.js"));
assert.equal(typeof overviewModule.renderOverview, "function");
assert.equal(typeof overviewModule.bindOverview, "function");
assert.equal(typeof guideModule.renderGuide, "function");
assert.equal(typeof guideModule.bindGuide, "function");
assert.equal(typeof routeModule.loadSectionAlongsideAsset, "function");
assert.equal(loadingModule.fieldErrorKey("title"), "__title");
assert.equal(loadingModule.fieldErrorKey("chat_model"), "chat_model");

assert.equal(routeModule.routeAssetKind("assistant/basics"), "agent-config");
assert.equal(routeModule.routeAssetKind("assistant/advanced"), "agent-config");
assert.equal(routeModule.routeAssetKind("capabilities/functions"), "agent-config");
assert.equal(routeModule.routeAssetKind("data-memory/conversations"), "agent-config");
assert.equal(routeModule.routeAssetKind("usage-maintenance/backup-restore"), "agent-config");
assert.equal(routeModule.routeAssetKind("usage-maintenance/retention"), "agent-config");
assert.equal(routeModule.routeAssetKind("capabilities/request-rules"), "request-rules");
assert.equal(routeModule.routeAssetKind("overview"), null);
assert.equal(
  routeModule.matchesRequestRuleSearch(
    {name:"Night", phrases:["good night", "bed time", "sleep now", "lights out", "hidden fifth phrase"], action_type:"local_action"},
    "fifth phrase",
  ),
  true,
);
assert.equal(
  routeModule.matchesRequestRuleSearch(
    {name:"Careful", phrases:["think carefully"], action_type:"model_routing"},
    "MODEL_ROUTING",
  ),
  true,
);

const localDateTime = "2026-09-02T19:15";
assert.equal(
  loadingModule.normalizeGuestModeTimestamp(localDateTime),
  new Date(localDateTime).toISOString(),
);
assert.equal(
  loadingModule.normalizeGuestModeTimestamp("2026-09-02T19:15:00+01:00"),
  "2026-09-02T19:15:00+01:00",
);
assert.equal(
  loadingModule.normalizeGuestModeTimestamp("2026-09-02T18:15:00Z"),
  "2026-09-02T18:15:00Z",
);
assert.equal(loadingModule.validatedImportMatches("document-a", "document-a"), true);
assert.equal(loadingModule.validatedImportMatches("document-a", "document-b"), false);
assert.equal(loadingModule.validatedImportMatches(null, "document-a"), false);

let resolveMutation;
let mutationCalls = 0;
const mutationControl = {
  tagName: "INPUT",
  disabled: false,
  dataset: {},
};
const mutationToasts = [];
const mutationPanel = {
  _toast: (...args) => mutationToasts.push(args),
};
const firstMutation = loadingModule.runFrontendMutation(
  mutationPanel,
  mutationControl,
  "save settings",
  () => {
    mutationCalls += 1;
    return new Promise((resolve) => { resolveMutation = resolve; });
  },
);
assert.equal(mutationControl.disabled, true);
assert.equal(mutationControl.dataset.eocMutationPending, "true");
const duplicateMutation = await loadingModule.runFrontendMutation(
  mutationPanel,
  mutationControl,
  "save settings",
  async () => { mutationCalls += 1; },
);
assert.equal(duplicateMutation, false);
assert.equal(mutationCalls, 1);
resolveMutation();
assert.equal(await firstMutation, true);
assert.equal(mutationControl.disabled, false);
assert.equal(mutationControl.dataset.eocMutationPending, undefined);
assert.deepEqual(mutationToasts, []);

const failedControl = {
  tagName: "INPUT",
  disabled: false,
  dataset: {},
};
const failedMutation = await loadingModule.runFrontendMutation(
  mutationPanel,
  failedControl,
  "save settings",
  async () => { throw new Error("network down"); },
);
assert.equal(failedMutation, false);
assert.equal(failedControl.disabled, false);
assert.deepEqual(mutationToasts.at(-1), ["Unable to save settings: network down", true]);

let currentView = "guide";
let sectionLoads = 0;
let renders = 0;
let resolveAsset;
let resolveSection;
const panel = {
  _busy: true,
  _error: null,
  _eocViewAssetToken: 1,
  _viewKey: () => currentView,
  _render: () => { renders += 1; },
};
const deferredAsset = new Promise((resolve) => { resolveAsset = resolve; });
const deferredSection = new Promise((resolve) => { resolveSection = resolve; });
const pendingLoad = routeModule.loadSectionAlongsideAsset(
  panel,
  false,
  () => {
    sectionLoads += 1;
    return deferredSection;
  },
  currentView,
  deferredAsset,
  panel._eocViewAssetToken,
);
assert.equal(sectionLoads, 1);
currentView = "assistant/basics";
panel._eocViewAssetToken += 1;
resolveAsset();
resolveSection("loaded");
assert.equal(await pendingLoad, undefined);
assert.equal(renders, 0);
assert.equal(panel._busy, true);
assert.equal(panel._error, null);

currentView = "guide";
panel._eocViewAssetToken += 1;
const supersededToken = panel._eocViewAssetToken;
let rejectSupersededAsset;
const supersededAsset = new Promise((_resolve, reject) => { rejectSupersededAsset = reject; });
const supersededLoad = routeModule.loadSectionAlongsideAsset(
  panel,
  false,
  () => {
    sectionLoads += 1;
    return Promise.resolve("loaded");
  },
  currentView,
  supersededAsset,
  supersededToken,
);
panel._eocViewAssetToken += 1;
rejectSupersededAsset(new Error("superseded lazy import failed"));
assert.equal(await supersededLoad, undefined);
assert.equal(renders, 0);
assert.equal(panel._error, null);

currentView = "usage-maintenance/request-debug";
panel._eocViewAssetToken += 1;
const currentToken = panel._eocViewAssetToken;
const failedLoad = routeModule.loadSectionAlongsideAsset(
  panel,
  false,
  () => {
    sectionLoads += 1;
    return Promise.resolve("loaded");
  },
  currentView,
  Promise.reject(new Error("lazy import failed")),
  currentToken,
);
assert.equal(await failedLoad, undefined);
assert.equal(panel._busy, false);
assert.match(panel._error, /Unable to load this frontend section: lazy import failed/);
assert.equal(renders, 1);

for (const name of ["management-actions.js", "management-route.js", "management-renderer.js"]) {
  const source = await readFile(frontend(name), "utf8");
  assert.doesNotMatch(source, /prototype\.|whenDefined|customElements/);
  assert.ok(!bootstrap.includes(`await import("./${name}")`));
}
