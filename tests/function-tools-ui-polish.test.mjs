import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const source = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js",
    import.meta.url,
  ),
  "utf8",
);
const renderer = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js", import.meta.url), "utf8");
const {renderTools} = await import("../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools.js");

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
  renderer,
  /\.function-group-card\.always-card[\s\S]*color-mix\(in srgb, var\(--primary-color\) 5%/,
  "Available on every request should share the normal Function Group tint",
);
assert.match(
  renderer,
  /\.function-group-card\.function-repair-attention[\s\S]*var\(--warning-color, #ff9800\)/,
  "Needs attention should use a distinct light warning treatment",
);
assert.match(
  renderer,
  /\.function-group-card > details \.tool-card[\s\S]*background:\s*var\(--card-background-color\)/,
  "member Function Tool cards should remain visually distinct from their collection",
);

const markup = renderTools({_e:String, _empty:String, _draft:{function_groups:[{id:"one",name:"One",functions:[]}]}});
assert.match(markup, /aria-label="Edit Function Group One"[^>]*>Edit<\/button>/);
assert.match(markup, /aria-label="Delete Function Group One"[^>]*>Delete<\/button>/);

console.log("Function Tools UI polish guards passed");
