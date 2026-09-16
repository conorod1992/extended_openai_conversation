import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const source = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js",
    import.meta.url,
  ),
  "utf8",
);

assert.match(
  source,
  /#tool-dialog\.tool-dialog[\s\S]*max-height:\s*calc\(100dvh - 32px\)[\s\S]*overflow:\s*hidden/,
  "Function Tool dialog should stay inside the viewport instead of creating a second scrollbar",
);
assert.match(
  source,
  /#tool-dialog \.tool-dialog-body[\s\S]*min-height:\s*0[\s\S]*overflow:\s*hidden/,
  "dialog body should delegate scrolling to the YAML editor",
);
assert.match(
  source,
  /#\$\{NATIVE_EDITOR_ID\}[\s\S]*cursor:\s*text/,
  "native YAML editor should use a text-editing cursor",
);

assert.match(
  source,
  /\.function-group-card\.always-card[\s\S]*color-mix\(in srgb, var\(--primary-color\) 5%/,
  "Available on every request should share the normal Function Group tint",
);
assert.match(
  source,
  /\.function-group-card\.function-repair-attention[\s\S]*var\(--warning-color, #ff9800\)/,
  "Needs attention should use a distinct light warning treatment",
);
assert.match(
  source,
  /\.function-group-card > details \.tool-card[\s\S]*background:\s*var\(--card-background-color\)/,
  "member Function Tool cards should remain visually distinct from their collection",
);

assert.match(source, /editButton\.textContent = "Edit"/);
assert.match(source, /deleteButton\.textContent = "Delete"/);
assert.match(source, /Edit Function Group \$\{group\.name\}/);
assert.match(source, /Delete Function Group \$\{group\.name\}/);

console.log("Function Tools UI polish guards passed");
