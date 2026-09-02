import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [loading, overview, guide, debug] = await Promise.all([
  readFile(frontend("management-loading-performance.js"), "utf8"),
  readFile(frontend("overview-page.js"), "utf8"),
  readFile(frontend("guide-page.js"), "utf8"),
  readFile(frontend("debug-management.js"), "utf8"),
]);

assert.match(loading, /_call\("overview", "summary"\)/);
assert.match(loading, /_call\("configuration", "save"/);
assert.doesNotMatch(loading, /_loadAgents\(panel\._agentId\)/);
assert.match(loading, /SCOPE_CACHE_TTL_MS = 30_000/);
assert.match(loading, /event\.stopImmediatePropagation\(\)/);
assert.match(loading, /panel\._viewKey\(\) === view && panel\._loadToken === loadToken/);
assert.match(loading, /Document changed\. Validate & preview again before importing\./);
assert.match(loading, /validatedImportMatches\(panel\._importDocument, current\)/);
assert.match(loading, /section === "guest_mode" && action === "update"/);
assert.match(loading, /button\.id === "guest-policy-save"/);
assert.match(loading, /button\.classList\.contains\("rule-duplicate"\)/);
assert.match(loading, /button\.classList\.contains\("rule-delete"\)/);
assert.match(loading, /input\?\.classList\?\.contains\("rule-enabled"\)/);
assert.match(loading, /this\._eocRuleSavePromise/);

assert.match(overview, /import\("\.\/overview-page-impl\.js"\)/);
assert.doesNotMatch(overview, /from "\.\/overview-page-impl\.js"/);
assert.match(guide, /import\("\.\/guide-page-impl\.js"\)/);
assert.doesNotMatch(guide, /from "\.\/guide-page-base\.js"/);
assert.match(debug, /import\("\.\/debug-panel\.js"\)/);
assert.doesNotMatch(debug, /^import "\.\/debug-panel\.js"/m);

const overviewModule = await import(frontend("overview-page.js"));
const guideModule = await import(frontend("guide-page.js"));
const loadingModule = await import(frontend("management-loading-performance.js"));
assert.equal(typeof overviewModule.renderOverview, "function");
assert.equal(typeof overviewModule.bindOverview, "function");
assert.equal(typeof guideModule.renderGuide, "function");
assert.equal(typeof guideModule.bindGuide, "function");
assert.equal(typeof loadingModule.loadSectionAfterAsset, "function");
assert.equal(loadingModule.fieldErrorKey("title"), "__title");
assert.equal(loadingModule.fieldErrorKey("chat_model"), "chat_model");

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
let continuedLoads = 0;
let renders = 0;
let resolveAsset;
const panel = {
  _busy: true,
  _error: null,
  _loadToken: 1,
  _viewKey: () => currentView,
  _render: () => { renders += 1; },
};
const deferredAsset = new Promise((resolve) => { resolveAsset = resolve; });
const pendingLoad = loadingModule.loadSectionAfterAsset(
  panel,
  false,
  () => { continuedLoads += 1; },
  currentView,
  deferredAsset,
  panel._loadToken,
);
currentView = "assistant/basics";
panel._loadToken += 1;
resolveAsset();
await pendingLoad;
assert.equal(continuedLoads, 0);
assert.equal(renders, 0);
assert.equal(panel._busy, true);
assert.equal(panel._error, null);

currentView = "guide";
panel._loadToken += 1;
let resolveSupersededAsset;
const supersededToken = panel._loadToken;
const supersededAsset = new Promise((resolve) => { resolveSupersededAsset = resolve; });
const supersededLoad = loadingModule.loadSectionAfterAsset(
  panel,
  false,
  () => { continuedLoads += 1; },
  currentView,
  supersededAsset,
  supersededToken,
);
panel._loadToken += 1;
resolveSupersededAsset();
await supersededLoad;
assert.equal(continuedLoads, 0);
assert.equal(renders, 0);

currentView = "usage-maintenance/request-debug";
panel._loadToken += 1;
let rejectAsset;
const rejectedToken = panel._loadToken;
const rejectedAsset = new Promise((_resolve, reject) => { rejectAsset = reject; });
const pendingFailure = loadingModule.loadSectionAfterAsset(
  panel,
  false,
  () => { continuedLoads += 1; },
  currentView,
  rejectedAsset,
  rejectedToken,
);
currentView = "assistant/basics";
panel._loadToken += 1;
rejectAsset(new Error("lazy import failed"));
await pendingFailure;
assert.equal(continuedLoads, 0);
assert.equal(renders, 0);
assert.equal(panel._busy, true);
assert.equal(panel._error, null);
