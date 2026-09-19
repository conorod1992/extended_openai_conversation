import assert from "node:assert/strict";

const elements = new Map();
globalThis.window = {location: {pathname: "/extended-openai/assistant/basics"}, addEventListener() {}, removeEventListener() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes: () => false}; } };
globalThis.customElements = {
  define(name, type) { elements.set(name, type); },
  get(name) { return elements.get(name); },
  whenDefined() { return Promise.resolve(); },
};
const frontend = (name) => new URL(`../custom_components/extended_openai_conversation_responses/frontend/${name}`, import.meta.url);
const {ExtendedOpenAIManagementPanel: Panel} = await import(frontend("management-panel.js"));
const {routeAssetPromise} = await import(frontend("management-route.js"));
await routeAssetPromise("assistant/basics");

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function panelFor(operation = async () => "loaded") {
  const panel = new Panel();
  panel._render = () => {};
  panel._loadSectionData = operation;
  return panel;
}
const realPerformance = globalThis.performance;
const descriptor = Object.getOwnPropertyDescriptor(globalThis, "performance");
const prefix = "extended-openai:load-section:";

// HA may replace the custom panel while work on the previous instance settles.
for (const order of [[0, 1], [1, 0]]) {
  realPerformance.clearMarks();
  const work = [deferred(), deferred()];
  const panels = work.map((item) => panelFor(() => item.promise));
  const outcomes = Promise.allSettled(panels.map((panel) => panel._loadSection()));
  const starts = realPerformance.getEntriesByType("mark").filter((item) => item.name.startsWith(prefix));
  work[order[0]].resolve("first");
  await new Promise((resolve) => setImmediate(resolve));
  work[order[1]].resolve("second");
  assert.deepEqual((await outcomes).map((item) => item.status), ["fulfilled", "fulfilled"]);
  assert.equal(new Set(starts.map((item) => item.name)).size, 2, "instances must not share global timing marks");
  assert.equal(realPerformance.getEntriesByType("mark").filter((item) => item.name.startsWith(prefix)).length, 0);
}

// The host, devtools or another component can clear marks during a request.
for (const fails of [false, true]) {
  const work = deferred();
  const originalError = new Error("original backend failure");
  const outcome = Promise.allSettled([panelFor(() => work.promise)._loadSection()]);
  realPerformance.clearMarks();
  if (fails) work.reject(originalError); else work.resolve("loaded");
  const [result] = await outcome;
  assert.equal(result.status, fails ? "rejected" : "fulfilled");
  if (fails) assert.equal(result.reason, originalError, "telemetry must preserve the original failure");
  else assert.equal(result.value, "loaded");
}

try {
  // Both modern and legacy measure calls, marking, and cleanup are optional.
  for (const throwsAt of ["start", "end", "measure", "clearMarks", "clearMeasures"]) {
    let marks = 0;
    Object.defineProperty(globalThis, "performance", {configurable: true, value: {
      mark() { marks++; if (throwsAt === "start" || (throwsAt === "end" && marks % 2 === 0)) throw new Error("timing unavailable"); },
      measure() { if (throwsAt === "measure") throw new Error("timing unavailable"); },
      clearMarks() { if (throwsAt === "clearMarks") throw new Error("timing unavailable"); },
      clearMeasures() { if (throwsAt === "clearMeasures") throw new Error("timing unavailable"); },
    }});
    const panel = panelFor();
    for (let i = 0; i < 102; i++) assert.equal(await panel._loadSection(), "loaded");
    assert.ok((panel._eocPerformanceMeasureIds?.length || 0) <= 100);
    const originalError = new Error("backend error must remain visible");
    await assert.rejects(panelFor(() => { throw originalError; })._loadSection(), (error) => error === originalError);
  }
} finally {
  Object.defineProperty(globalThis, "performance", descriptor);
  realPerformance.clearMarks();
  realPerformance.clearMeasures();
}
console.log("Cross-instance timing isolation and non-fatal instrumentation tests passed");
