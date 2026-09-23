import assert from "node:assert/strict";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

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
assert.equal(routeModule.routeAssetKind("capabilities/functions"), "agent-config-tools");
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

// Loading publishes the complete feature only after its dependencies resolve.
assert.equal(routeModule.getConfigurationEditor(), undefined);
assert.equal(routeModule.routeFeaturesReady("assistant/basics"), false);
assert.equal(routeModule.routeFeaturesReady("capabilities/request-rules"), false);
await Promise.all([routeModule.routeAssetPromise("assistant/basics"), routeModule.routeAssetPromise("assistant/basics")]);
const editor = routeModule.getConfigurationEditor();
assert.equal(routeModule.routeFeaturesReady("assistant/basics"), true);
for (const name of ["renderConfiguration", "bindConfiguration", "configurationDialogs"]) assert.equal(typeof editor[name], "function", name);
for (const name of ["renderTools", "bindTools", "reconcileTools"]) assert.equal(editor[name], undefined, name);
await routeModule.routeAssetPromise("assistant/basics");
assert.equal(routeModule.getConfigurationEditor(), editor);
assert.equal(routeModule.getConfigurationTools(), undefined);
await routeModule.routeAssetPromise("capabilities/functions");
const toolsEditor = routeModule.getConfigurationTools();
assert.equal(routeModule.routeFeaturesReady("capabilities/functions"), true);
for (const name of ["renderTools", "bindTools", "configurationDialogs", "reconcileTools"]) assert.equal(typeof toolsEditor[name], "function", name);
assert.equal(routeModule.getRouteFeature("capabilities/request-rules"), undefined);
await routeModule.routeAssetPromise("capabilities/request-rules");
const rules = routeModule.getRouteFeature("capabilities/request-rules");
assert.equal(routeModule.routeFeaturesReady("capabilities/request-rules"), true);
for (const name of ["renderRequestRules", "bindRequestRules", "requestRulesDialog", "reconcileRequestRules"]) assert.equal(typeof rules[name], "function", name);
await routeModule.routeAssetPromise("capabilities/request-rules");
assert.equal(routeModule.getRouteFeature("capabilities/request-rules"), rules);

// Specialized configuration modules stay cold until their owning route loads.
assert.equal(editor.restoreDialog, undefined);
for (const [view, exports] of [
  ["assistant/prompt-context", ["renderExposedAttributeSettings", "bindExposedAttributeSettings"]],
  ["usage-maintenance/backup-restore", ["renderBackupTransferPanel", "renderRestoreTransferDialog", "bindBackupTransfer"]],
]) {
  assert.equal(routeModule.getRouteFeature(view), undefined);
  assert.equal(routeModule.routeFeaturesReady(view), false);
  await routeModule.routeAssetPromise(view);
  const feature = routeModule.getRouteFeature(view);
  assert.equal(routeModule.routeFeaturesReady(view), true);
  for (const name of exports) assert.equal(typeof feature[name], "function", name);
  await routeModule.routeAssetPromise(view);
  assert.equal(routeModule.getRouteFeature(view), feature);
}
