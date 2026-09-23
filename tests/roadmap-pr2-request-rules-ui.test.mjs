import assert from "node:assert/strict";
import test from "node:test";

const frontend = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/",
  import.meta.url,
);

const {renderRequestRules} = await import(new URL("request-rules-ui.js", frontend));
const {renderGuide} = await import(new URL("guide-page.js", frontend));
const panel = {_e:(value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("'", "&#39;"), _empty:String};

test("Request Rules UI exposes revision-safe move controls", async () => {
  const markup = renderRequestRules({...panel, _result:{rules:[{id:"one",name:"One",enabled:true,phrases:["one"],action_type:"local_action",action:{},match_type:"equals"}]}});

  assert.match(markup, /class=\"secondary rule-move\"/);
  assert.match(markup, /data-direction=\"up\" disabled/);
  assert.match(markup, /data-direction=\"down\" disabled/);
});

test("Request Rules UI explains AI-routing command semantics", async () => {
  const source = renderRequestRules(panel);

  assert.match(source, /routing command by default/i);
  assert.match(source, /Continue to AI/i);
  assert.match(source, /routing hints inside a normal request/i);
  assert.match(source, /Reset for this request only/i);
  assert.match(source, /first match is used/i);
});

test("Guide explains broad versus complete AI-routing matches", async () => {
  const source = renderGuide(panel);

  assert.match(source, /original request/i);
  assert.match(source, /Hassil/i);
  assert.match(source, /deterministic rule/i);
  assert.match(source, /Fuzzy matching/i);
});
