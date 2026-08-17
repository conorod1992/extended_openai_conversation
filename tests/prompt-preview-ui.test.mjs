import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { copyTextToClipboard } from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";

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
assert.match(editor, /Previewed Request Content/);
assert.match(editor, /copy-request-section/);
assert.match(editor, /copyTextToClipboard/);

let modernCopied = "";
await copyTextToClipboard("modern", { clipboard: { writeText: async (text) => { modernCopied = text; } } });
assert.equal(modernCopied, "modern");

let fallbackCopied = false;
let fallbackRemoved = false;
const fallbackElement = {
  style: {},
  setAttribute() {},
  select() {},
  remove() { fallbackRemoved = true; },
};
const fallbackDocument = {
  body: { appendChild() {} },
  createElement() { return fallbackElement; },
  execCommand(command) { fallbackCopied = command === "copy"; return fallbackCopied; },
};
await copyTextToClipboard("fallback", {}, fallbackDocument);
assert.equal(fallbackElement.value, "fallback");
assert.equal(fallbackCopied, true);
assert.equal(fallbackRemoved, true);

fallbackCopied = false;
await copyTextToClipboard("denied", { clipboard: { writeText: async () => { throw new Error("denied"); } } }, fallbackDocument);
assert.equal(fallbackCopied, true);
