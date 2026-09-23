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
assert.match(html, /class="notice local-handling-warning" role="status"/);
assert.match(html, /<details class="local-handling-help">/);
assert.doesNotMatch(html, /notice local-handling-explainer/);
assert.match(html, /JarvisV2/);
