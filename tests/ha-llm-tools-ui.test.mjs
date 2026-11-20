import assert from "node:assert/strict";
import { bindHALlmTools, filterHATools, haToolName, isHALlmTool, renderHAToolCard } from "../custom_components/extended_openai_conversation_responses/frontend/ha-llm-tools.js";
import {categorizeFunctionTools, synchronizePersistedFunctions} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js";

const tool = {spec: {name: "ha_reference_id"}, function: {type: "ha_llm", tool_name: "native_tool", source_id: "source", api_id: "assist"}, enabled: true};
const panel = {_e: value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;"), _haCatalog: {saved: {ha_reference_id: {available: false, source: "source <unsafe>", description: "Unavailable"}}}};
assert.equal(isHALlmTool(tool), true);
assert.equal(haToolName(tool), "native_tool");
const card = renderHAToolCard(panel, tool, 0);
assert.match(card, /native_tool/);
assert.match(card, /Unavailable/);
assert.match(card, /Remove/);
assert.match(card, /tool-enabled/);
assert.match(card, /&lt;unsafe>/);
assert.doesNotMatch(card, /edit-tool|duplicate-tool|YAML/);
assert.match(renderHAToolCard(panel, {...tool, enabled: false}, 1), /is-disabled/);
const catalog = [{name: "First", description: "Weather", source: "One", already_added: true}, {name: "Second", description: "Weather", source: "Two"}, {name: "Third", description: "Lights", source: "Two"}];
assert.deepEqual(filterHATools(catalog, "weather", ["Two"]), [catalog[1]]);
assert.deepEqual(filterHATools(catalog, "", ["One", "Two"]), catalog);
assert.deepEqual(filterHATools(catalog, "unknown"), []);
const groups = [{id: "remote", name: "Remote", description: "Remote", loading_mode: "on_demand", enabled: true, functions: [tool.spec.name]}];
const categorized = categorizeFunctionTools({functions: [tool], function_groups: groups});
assert.equal(categorized.alwaysAvailable.length, 0);
assert.equal(categorized.groups.length, 1);
const syncPanel = {_configData: {config: {prompt: "saved"}}, _draft: {prompt: "unsaved"}, _syncConfigDirty() { this.synced = true; }};
synchronizePersistedFunctions(syncPanel, {functions: [tool], function_groups: groups});
assert.deepEqual(syncPanel._draft.functions, [tool]);
assert.equal(syncPanel._draft.prompt, "unsaved");
assert.equal(syncPanel.synced, true);

{
  const previousNow = Date.now;
  const now = 100_000;
  Date.now = () => now;
  try {
    for (const [age, sameAgent, refresh] of [
      [null, true, true],
      [-1, true, true],
      [0, true, false],
      [30_000, true, false],
      [30_001, true, true],
      [0, false, true],
    ]) {
      const calls = [];
      const host = {};
      const catalogPanel = {
        _agentId: "agent",
        _draft: {functions: [tool]},
        _haCatalogAgent: sameAgent ? "agent" : "other",
        _haCatalogLoadedAt: age === null ? undefined : now - age,
        shadowRoot: {querySelector: selector => selector === ".tools-surface" ? host : null},
        async _call(...args) { calls.push(args); return {}; },
        _render() {},
      };
      bindHALlmTools(catalogPanel);
      await catalogPanel._haCatalogLoad?.promise;
      assert.deepEqual(calls, refresh ? [["tools", "ha_catalog"]] : [],
        `catalogue refresh at age ${age} with matching owner ${sameAgent}`);
      if (refresh) {
        assert.equal(catalogPanel._haCatalogAgent, "agent");
        assert.equal(catalogPanel._haCatalogLoadedAt, now);
      }
    }
  } finally {
    Date.now = previousNow;
  }
}
