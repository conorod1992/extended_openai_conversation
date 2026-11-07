import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {bindBackupTransfer} from "../custom_components/extended_openai_conversation_responses/frontend/backup-route-ui.js";

const source = fileURLToPath(new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js", import.meta.url));
const functionsSource = readFileSync(source, "utf8");
assert.match(functionsSource, /padding-top:18px}\$\{COMPACT_CHOICE_STYLE\}/);
assert.doesNotMatch(functionsSource, /padding-top:18px}\.\$\{COMPACT_CHOICE_STYLE\}/);

const originalDocument = globalThis.document;
const styles = [];
const root = {
  querySelector(selector) {
    if (selector === 'style[data-eoc-feature-style="compact-choices"]') return styles[0] || null;
    return null;
  },
  append(style) {styles.push(style);},
};
globalThis.document = {
  createElement(tagName) {
    assert.equal(tagName, "style");
    return {dataset: {}, textContent: ""};
  },
};
try {
  const panel = {shadowRoot: root, _agentId: "agent", _viewKey: () => "usage-maintenance/backup-restore"};
  bindBackupTransfer(panel);
  bindBackupTransfer(panel);
  assert.equal(styles.length, 1);
  assert.equal(styles[0].dataset.eocFeatureStyle, "compact-choices");
  assert.match(styles[0].textContent, /\.group-function-choices\{display:grid;/);
  assert.match(styles[0].textContent, /overflow:auto/);
} finally {
  globalThis.document = originalDocument;
}
console.log("Compact-choice frontend source contract passed");
