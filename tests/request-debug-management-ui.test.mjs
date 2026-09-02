import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const navigation = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js", import.meta.url),
  "utf8",
);
const integration = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/debug-management.js", import.meta.url),
  "utf8",
);
const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
const backend = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/debug_ui.py", import.meta.url),
  "utf8",
);

assert.match(navigation, /id: "request-debug", label: "Request debugging"/);
assert.doesNotMatch(navigation, /import\("\.\/debug-management\.js"\)/);
assert.match(bootstrap, /await import\("\.\/debug-management\.js"\)/);
assert.match(integration, /usage-maintenance\/request-debug/);
assert.match(integration, /const originalLoadSection = prototype\._loadSection/);
assert.match(integration, /return ensureDebugPanel\(\)/);
assert.match(integration, /this\._eocDebugLoadToken !== token/);
assert.match(integration, /HA session/);
assert.match(integration, /Prompt-cache hits can be shared across separate sessions and do not imply shared conversation history/);
assert.match(integration, /this\._data\?\.is_admin === true/);
assert.doesNotMatch(backend, /async_register_panel/);
assert.match(backend, /debug-management\.js/);
