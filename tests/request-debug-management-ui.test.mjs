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
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-route.js", import.meta.url),
  "utf8",
);
const backend = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/debug_ui.py", import.meta.url),
  "utf8",
);
const projection = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/debug_management_projection.py", import.meta.url),
  "utf8",
);

assert.match(navigation, /id: "request-debug", label: "Request debugging"/);
assert.doesNotMatch(navigation, /import\("\.\/debug-management\.js"\)/);
const debugPanel = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/debug-panel.js", import.meta.url), "utf8");
assert.match(bootstrap, /import\("\.\/debug-management\.js"\)/);
assert.doesNotMatch(integration, /prototype\.|installManagementSection|installDebugPresentation/);
assert.match(integration, /usage-maintenance\/request-debug/);
assert.match(integration, /export function loadRequestDebug\(panel/);
assert.doesNotMatch(integration, /prototype\._loadSection/);
assert.match(integration, /return ensureDebugPanel\(\)/);
assert.match(integration, /panel\._eocDebugLoadToken !== token/);
assert.match(integration, /HA session/);
assert.match(debugPanel, /Prompt-cache hits can be shared across separate sessions and do not imply shared conversation history/);
assert.match(integration, /panel\._data\?\.is_admin !== true/);
assert.match(debugPanel, /provider_offset:/);
assert.match(debugPanel, /provider_limit: DEBUG_PROVIDER_PAGE_LIMIT/);
assert.match(debugPanel, /Previous requests/);
assert.match(debugPanel, /Next requests/);
assert.match(debugPanel, /Copy visible page/);
assert.match(debugPanel, /Copy first page/);
assert.match(debugPanel, /bounded\/truncated management view/);
assert.doesNotMatch(integration, /Entire debug log copied/);
assert.doesNotMatch(backend, /async_register_panel/);
assert.match(backend, /async_register_frontend_assets/);
assert.match(backend, /debug_trace_page/);
assert.match(backend, /vol\.Optional\("provider_offset"\): int/);
assert.match(backend, /vol\.Optional\("provider_limit"\): int/);
assert.doesNotMatch(backend, /json\.dumps\(trace/);
assert.match(projection, /MANAGEMENT_DEBUG_PAGE_CHARACTERS/);
assert.match(projection, /page_budget = _ProjectionBudget/);
assert.match(projection, /"provider_requests": provider_meta/);
