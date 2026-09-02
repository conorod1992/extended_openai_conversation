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
assert.match(loading, /panel\._viewKey\(\) !== view/);

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

let currentView = "guide";
let continuedLoads = 0;
let renders = 0;
let resolveAsset;
const deferredAsset = new Promise((resolve) => { resolveAsset = resolve; });
const panel = {
  _busy: true,
  _error: null,
  _viewKey: () => currentView,
  _render: () => { renders += 1; },
};
const pendingLoad = loadingModule.loadSectionAfterAsset(
  panel,
  false,
  () => { continuedLoads += 1; },
  currentView,
  deferredAsset,
);
currentView = "assistant/basics";
resolveAsset();
await pendingLoad;
assert.equal(continuedLoads, 0);
assert.equal(renders, 0);
assert.equal(panel._busy, true);
assert.equal(panel._error, null);

currentView = "usage-maintenance/request-debug";
let rejectAsset;
const rejectedAsset = new Promise((_resolve, reject) => { rejectAsset = reject; });
const pendingFailure = loadingModule.loadSectionAfterAsset(
  panel,
  false,
  () => { continuedLoads += 1; },
  currentView,
  rejectedAsset,
);
currentView = "assistant/basics";
rejectAsset(new Error("lazy import failed"));
await pendingFailure;
assert.equal(continuedLoads, 0);
assert.equal(renders, 0);
assert.equal(panel._busy, true);
assert.equal(panel._error, null);
