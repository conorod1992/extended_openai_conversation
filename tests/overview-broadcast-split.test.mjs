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
const css = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management.css", import.meta.url),
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

assert.match(css, /\/\* Overview Broadcast \*\//);
assert.match(css, /#broadcast-card/);
assert.match(css, /\.broadcast-toggle-row/);
