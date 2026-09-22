import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const panel = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url),
  "utf8",
);
const draftNavigation = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-draft-navigation.js", import.meta.url),
  "utf8",
);
const metadata = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-setting-metadata.js", import.meta.url),
  "utf8",
);
const vite = await readFile(new URL("../frontend/vite.config.ts", import.meta.url), "utf8");

assert.doesNotMatch(panel, /from "\.\/management-navigation-search\.js"/);
assert.doesNotMatch(panel, /from "\.\/management-setting-metadata\.js"/);
assert.doesNotMatch(panel, /from "\.\/usage-data\.js"/);
assert.match(panel, /import\("\.\/management-navigation-search\.js"\)/);
assert.match(panel, /await import\("\.\/usage-data\.js"\)/);

assert.match(
  draftNavigation,
  /from "\.\/management-config-destinations\.js"/,
  "dirty navigation ownership must not pull rich settings metadata into startup",
);
assert.match(
  metadata,
  /from "\.\/management-config-destinations\.js"/,
  "settings metadata should re-export shared destination helpers",
);

const coreHelpers = vite.match(/const coreHelpers = \[([\s\S]*?)\];/)?.[1] || "";
assert.doesNotMatch(coreHelpers, /usage-data\.js/);
assert.match(coreHelpers, /usage-format\.js/);

assert.match(panel, /setTimeout\(\(\) => \{[\s\S]*?this\._warmNavigationTarget\(target\);[\s\S]*?\}, 100\)/);
assert.match(panel, /root\.addEventListener\("pointerout"/);
assert.match(panel, /root\.addEventListener\("pointerdown"/);
assert.match(panel, /root\.addEventListener\("focusin"/);
