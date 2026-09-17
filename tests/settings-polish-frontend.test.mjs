import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontendRoot = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/",
  import.meta.url,
);
const polish = await readFile(new URL("management-settings-polish.js", frontendRoot), "utf8");
const bootstrap = await readFile(new URL("management-bootstrap.js", frontendRoot), "utf8");
const conversationLabel = await readFile(
  new URL("management-conversation-default-label.js", frontendRoot),
  "utf8",
);

assert.match(
  bootstrap,
  /"\.\/management-settings-polish\.js"/,
  "the settings polish layer must be preloaded through the management bootstrap",
);
assert.match(
  bootstrap,
  /await import\("\.\/management-settings-polish\.js"\);/,
  "the settings polish layer must be evaluated through the controlled bootstrap sequence",
);
assert.doesNotMatch(
  conversationLabel,
  /management-settings-polish\.js/,
  "management enhancements should not introduce nested module imports outside the bootstrap loader",
);

assert.match(
  polish,
  /grid-template-columns:repeat\(auto-fit,minmax\(min\(260px,100%\),1fr\)\)!important/,
  "Guide quick actions should adapt their column count instead of overflowing the page",
);
assert.match(
  polish,
  /#config-conversation_continuity\{height:42px;min-height:42px\}/,
  "conversation continuity should use the normal select height",
);

for (const redundant of ["Advanced", "Adds context", "Stores data", "Stores shared data", "Stores temporary data"]) {
  assert.match(polish, new RegExp(`"${redundant}"`));
}
assert.match(
  polish,
  /Recommended default: Automatic \(Auto\)/,
  "Provider API format should collapse duplicate default/recommended guidance into one badge",
);

assert.match(polish, /Model data & defaults/);
assert.match(polish, /Reset this assistant's parameters/);
assert.match(polish, /Check for model data updates/);
assert.match(polish, /Use bundled model data/);
assert.match(polish, /data-model-data="update"/);
assert.match(polish, /data-model-data="reset"/);
assert.match(polish, /Automatic update checks run daily/);
