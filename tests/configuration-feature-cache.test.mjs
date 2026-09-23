import assert from "node:assert/strict";
import {renderConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";

import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
await Promise.all(["assistant/basics", "assistant/voice", "capabilities/home-assistant", "data-memory/conversations", "usage-maintenance/retention"].map((view) => routeAssetPromise(view)));
let escapes = 0;
const panel = {_e: value => { escapes++; return String(value ?? ""); }, _titleCase: String,
  _agentId: "one", _draft: {}, _result: {config: {}, options: {}, defaults: {}},
  _configSections: ["archive"], _viewKey: () => "data-memory/conversations"};
const render = presentation => renderConfiguration(panel, presentation);
const first = render();
const renderedEscapes = escapes;
assert.ok(renderedEscapes > 0);
assert.equal(render(), first);
assert.equal(escapes, renderedEscapes, "repeat clean render reuses markup");

for (const invalidate of [
  () => { panel._result = {...panel._result}; },
  () => { panel._draft = {...panel._draft}; },
  () => { panel._agentId = "two"; },
  () => { panel._modelCatalogData = {}; },
  () => { panel._result.model_capabilities = {}; },
  () => { panel._baseScopes = []; },
  () => { panel._data = {scopes: []}; },
]) {
  const before = escapes;
  invalidate(); render();
  assert.ok(escapes > before, "changed dependencies invalidate cached markup");
}

panel._configDirty = true;
render();
let before = escapes;
render();
assert.ok(escapes > before, "dirty drafts always render");
panel._configDirty = false;
panel._configSections = ["model"];
render(); before = escapes; render();
assert.ok(escapes > before, "model-dependent sections remain uncached");

panel._configSections = ["voice"];
const voice = () => "<p>Voice identity one</p>";
assert.match(render({voiceIdentity: voice}), /Voice identity one/);
before = escapes;
render({voiceIdentity: voice});
assert.equal(escapes, before);
assert.match(render({voiceIdentity: () => "<p>Voice identity two</p>"}), /Voice identity two/);
