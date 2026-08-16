import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const editor = readFileSync(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url),
  "utf8",
);

assert.match(editor, /id="preview-request">Preview effective request/);
assert.match(editor, /id="prompt-preview-dialog"/);
assert.match(editor, /class="yaml-editor request-preview-output" readonly/);
assert.match(editor, /id="copy-prompt-preview"/);
assert.match(editor, /User input and conversation history are excluded/);
assert.match(editor, /Include current date & time/);
assert.match(editor, /Include exposed devices/);
assert.match(editor, /Advanced context formatting/);
assert.match(editor, /Saved by Function Groups/);
assert.match(editor, /copy-request-section/);
assert.match(editor, /navigator\.clipboard\.writeText/);
