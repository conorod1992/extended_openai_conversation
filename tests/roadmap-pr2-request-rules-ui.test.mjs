import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const frontend = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/",
  import.meta.url,
);

const readFrontend = (name) => readFile(new URL(name, frontend), "utf8");

test("Request Rules UI exposes revision-safe move controls", async () => {
  const source = await readFrontend("request-rules-ui.js");

  assert.match(source, /class=\"secondary rule-move\"/);
  assert.match(source, /data-direction=\"up\"/);
  assert.match(source, /data-direction=\"down\"/);
  assert.match(source, /panel\._call\("request_rules", "move"/);
  assert.match(source, /revision: panel\._result\?\.revision/);
});

test("Request Rules UI explains AI-routing command semantics", async () => {
  const source = await readFrontend("request-rules-ui.js");

  assert.match(source, /acknowledged locally and apply to the rest of the current conversation/);
  assert.match(source, /original request unchanged/);
  assert.match(source, /matched words are not stripped/);
  assert.match(source, /final tie-breaker after match type and phrase specificity/);
});

test("Guide explains broad versus complete AI-routing matches", async () => {
  const source = await readFrontend("guide-page-impl.js");

  assert.match(source, /entire original request is still sent unchanged/);
  assert.match(source, /complete routing command/);
  assert.match(source, /acknowledges it locally/);
  assert.match(source, /saved order is the final tie-breaker/);
  assert.match(source, /does not override match type or phrase specificity/);
});
