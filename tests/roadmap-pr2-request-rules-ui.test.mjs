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

test("Request Rules links to its Guide topic instead of repeating routing help", async () => {
  const source = renderRequestRules(panel);

  assert.match(source, /data-guide-topic="request-rules">Learn how routing works/);
  assert.doesNotMatch(source, /rule-routing-help|<summary>Learn how routing works<\/summary>/);
});

test("Guide explains current Request Rules matching and AI handoff", async () => {
  const source = renderGuide(panel);

  assert.match(source, /Original request/i);
  assert.match(source, /Captured value/i);
  assert.match(source, /ExtendedOpenAI sentence patterns/i);
  assert.match(source, /global priority order/i);
  assert.match(source, /Fuzzy matching/i);
  assert.doesNotMatch(source, /Home Assistant sentence patterns|Hassil sentence format/i);
});
