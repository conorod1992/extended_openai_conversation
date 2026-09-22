import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const frontend = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/",
  import.meta.url,
);

const readFrontend = (name) => readFile(new URL(name, frontend), "utf8");
const {renderRequestRules} = await import(new URL("request-rules-ui.js", frontend));
const {renderGuide} = await import(new URL("guide-page.js", frontend));
const panel = {_e:(value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll("'", "&#39;"), _empty:String};

test("Request Rules UI exposes revision-safe move controls", async () => {
  const source = await readFrontend("request-rules-ui-core.js");
  const markup = renderRequestRules({...panel, _result:{rules:[{id:"one",name:"One",enabled:true,phrases:["one"],action_type:"local_action",action:{},match_type:"equals"}]}});

  assert.match(markup, /class=\"secondary rule-move\"/);
  assert.match(markup, /data-direction=\"up\" disabled/);
  assert.match(markup, /data-direction=\"down\" disabled/);
  assert.match(source, /panel\._call\("request_rules","move"/);
  assert.match(source, /revision:panel\._result\?\.revision/);
});

test("Request Rules UI explains AI-routing command semantics", async () => {
  const source = renderRequestRules(panel);

  assert.match(source, /acknowledged locally and apply to the rest of the current conversation/);
  assert.match(source, /original request (?:unchanged )?to the provider(?: unchanged)?/);
  assert.match(source, /matched words are not stripped/);
  assert.match(source, /rules are evaluated from top to bottom and the first matching rule wins/);
});

test("Guide explains broad versus complete AI-routing matches", async () => {
  const source = renderGuide(panel);

  assert.match(source, /entire original request is still sent unchanged/);
  // The former replacement containing an apostrophe never matched escaped
  // browser HTML. Characterize shipped copy rather than dead replacement text.
  assert.match(source, /Home Assistant&#39;s Hassil sentence format/);
  assert.match(source, /Named Hassil expansions/);
  assert.match(source, /evaluated from top to bottom in the order shown/);
  assert.match(source, /first deterministic rule that matches wins/);
  assert.match(source, /Fuzzy matching is considered only if no deterministic rule matches/);
});
