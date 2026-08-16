import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const editor = readFileSync(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url),
  "utf8",
);

assert.match(editor, /id="preview-prompt">Preview effective prompt/);
assert.match(editor, /id="prompt-preview-dialog"/);
assert.match(editor, /id="prompt-preview-output"[^>]*readonly/);
assert.match(editor, /id="copy-prompt-preview"/);
assert.match(editor, /User input and conversation history are not included/);
assert.match(editor, /navigator\.clipboard\.writeText/);
