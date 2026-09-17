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
  /\[data-eoc-guide-layout\]\{grid-template-columns:minmax\(0,1fr\)!important\}/,
  "Guide content should stay within the available page width",
);
assert.match(
  polish,
  /\[data-eoc-guide-layout\]>\*\{min-width:0;max-width:100%\}/,
  "Guide children should be allowed to shrink instead of forcing horizontal overflow",
);
assert.match(
  polish,
  /grid-template-columns:repeat\(auto-fit,minmax\(min\(260px,100%\),1fr\)\)!important/,
  "Guide quick actions should adapt their column count instead of overflowing the page",
);
assert.match(
  polish,
  /\.comparison-table\{min-width:0;max-width:100%;overflow-x:auto\}/,
  "wide comparison tables should scroll locally rather than widening the whole Guide",
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

assert.match(polish, /Model capability data/);
assert.match(polish, /It checks for newer data daily but applies changes only when you approve them/);
assert.match(polish, /Check for updates/);
assert.match(polish, /Apply available update/);
assert.match(polish, /Restore bundled data/);
assert.match(polish, /data-model-data="check"/);
assert.match(polish, /data-model-data="apply"/);
assert.match(polish, /data-model-data="reset"/);
assert.match(
  polish,
  /Future checks will not replace it automatically/,
  "restoring bundled data should be described as a persistent rollback, not an automatic-update toggle",
);
assert.doesNotMatch(
  polish,
  /data-model-data="update"[^\]]/,
  "the old one-step update action should not be required by the settings polish layer",
);
