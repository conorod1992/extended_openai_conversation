import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const overview = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js", import.meta.url),
  "utf8",
);
const broadcast = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-broadcast.js", import.meta.url),
  "utf8",
);

assert.match(overview, /import\("\.\/overview-broadcast\.js"\)/);
assert.match(overview, /id="broadcast-card"/);
assert.match(overview, /Loading Broadcast…/);
assert.doesNotMatch(overview, /function broadcastMarkup/);
assert.doesNotMatch(overview, /function bindBroadcastControls/);
assert.doesNotMatch(overview, /const WS_BROADCAST/);
assert.doesNotMatch(overview, /\.broadcast-toggle-row\{/);

assert.match(broadcast, /const WS_BROADCAST/);
assert.match(broadcast, /function broadcastMarkup/);
assert.match(broadcast, /function bindBroadcastControls/);
assert.match(broadcast, /export function bindBroadcast/);
assert.match(broadcast, /panel\._viewKey\?\.\(\) !== "overview"/);


// Execute the real handler with a rejected module load, including stale views.
const bindSource = overview.slice(overview.indexOf("export function bindOverview"))
  .replace("export function", "function")
  .replace('import("./overview-broadcast.js")', 'Promise.reject(new Error("chunk unavailable"))');
const bindOverview = new Function(`${bindSource}; return bindOverview;`)();
const host = {innerHTML: "Loading Broadcast…"};
const panel = {
  shadowRoot: {querySelectorAll: () => [], querySelector: () => host},
  _viewKey: () => "overview", _e: (text) => text,
};
bindOverview(panel, Promise.resolve({}));
await new Promise((resolve) => setImmediate(resolve));
assert.match(host.innerHTML, /Unable to load Broadcast: chunk unavailable/);
host.innerHTML = "Other page";
panel._viewKey = () => "other";
bindOverview(panel, Promise.resolve({}));
await new Promise((resolve) => setImmediate(resolve));
assert.equal(host.innerHTML, "Other page");
