import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url),
  "utf8",
);

const updateBody = source.match(/_updateGuestMode\(now = false\) \{([\s\S]*?)\n  \}\n\n  _disableGuestMode/)?.[1] || "";
const disableBody = source.match(/_disableGuestMode\(\) \{([\s\S]*?)\n  \}\n\n  _diagnostics/)?.[1] || "";

assert.match(source, /_patchGuestModeStatus\(agentId, status\)/);
assert.match(source, /_refreshGuestModeMutation\(agentId, mutationResult\)/);
assert.match(source, /this\._call\("guest_mode", "get"\)/);
assert.doesNotMatch(updateBody, /_loadAgents/);
assert.doesNotMatch(disableBody, /_loadAgents/);
