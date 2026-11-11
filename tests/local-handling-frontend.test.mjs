import assert from "node:assert/strict";

import {renderLocalHandling} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js";

const escape = value => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

const panel = {
  _e: escape,
  _result: {
    local_handling: {
      supported: true,
      pipeline_conflicts: [{name: "JarvisV2"}],
      intents: [{intent: "HassTurnOn", label: "Turn on"}],
    },
  },
};
const config = {
  local_intents_enabled: true,
  local_intent_exclusions: [],
  local_intent_delayed_commands_to_ai: false,
};

const html = renderLocalHandling(panel, config);

const toggle = html.indexOf("Use Extended OpenAI local handling");
const warning = html.indexOf("Home Assistant is already handling some commands first");
const help = html.indexOf("What's the difference from Home Assistant's");
const dependent = html.indexOf('data-dependent="local_intents_enabled"');

assert.ok(toggle >= 0, "the primary local-handling control should render");
assert.ok(warning > toggle, "the current-state warning should follow the primary control");
assert.ok(help > warning, "background explanation should be secondary to the active warning");
assert.ok(dependent > help, "dependent command choices should follow the guidance");
assert.match(html, /class="notice warning local-handling-warning" role="status"/);
assert.match(html, /<details class="local-handling-help">/);
assert.doesNotMatch(html, /notice local-handling-explainer/);
assert.match(html, /JarvisV2/);


panel._result.local_handling.intents = [
  {intent:"HassBroadcast",label:"Broadcast",available:false},
  {intent:"HassMissing",label:"Missing",available:false},
];
const legacy = renderLocalHandling(panel, {...config,local_intents_enabled:false,local_intent_exclusions:["HassBroadcast"]});
assert.match(legacy, /legacy Broadcast opt-out also applies while local handling is off/);
assert.match(legacy, /Legacy Broadcast shortcut opt-out; EOAI never delegates this HA intent/);
assert.equal(legacy.split("Not currently available in Home Assistant").length, 2, "only the genuinely missing intent uses HA availability wording");
assert.match(legacy, /value="HassBroadcast" checked/);
