import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {TOOLBAR_STYLE} from "../custom_components/extended_openai_conversation_responses/frontend/management-toolbar-layout.js";

const source = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/management-toolbar-layout.js",
    import.meta.url,
  ),
  "utf8",
);

assert.match(
  source,
  /if \(settingsSearch\) header\.append\(settingsSearch\)/,
  "settings search should live in the page header",
);
assert.match(
  source,
  /contextRow\.append\(agentPicker\);\s*topNav\.before\(contextRow\)/,
  "assistant picker should establish context immediately before primary navigation",
);
assert.doesNotMatch(
  source,
  /toolbar\.append\(agentPicker\)|toolbar\.append\(settingsSearch\)/,
  "assistant and search must not be recombined into the old shared toolbar card",
);
assert.match(
  TOOLBAR_STYLE,
  /header \.global-search\.eoc-global-search\{[\s\S]*?width:min\(380px,100%\)/,
  "desktop search should remain compact in the page header",
);
assert.match(
  TOOLBAR_STYLE,
  /\.eoc-agent-context-row \.agent-picker\.eoc-agent-context\{[\s\S]*?padding:0;[\s\S]*?border:0;[\s\S]*?background:transparent;[\s\S]*?box-shadow:none/,
  "assistant context should override the nested-card treatment",
);
assert.match(
  TOOLBAR_STYLE,
  /@media \(max-width:800px\)[\s\S]*?header\{[\s\S]*?flex-direction:column/,
  "narrow layouts should stack the header controls",
);
assert.match(
  TOOLBAR_STYLE,
  /@media \(max-width:800px\)[\s\S]*?header \.global-search\.eoc-global-search\{[\s\S]*?width:100%/,
  "narrow layouts should make settings search full width",
);
