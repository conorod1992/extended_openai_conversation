import assert from "node:assert/strict";
import { bindHALlmTools, filterHATools, haToolName, isHALlmTool, renderHAToolCard } from "../custom_components/extended_openai_conversation_responses/frontend/ha-llm-tools.js";
import {categorizeFunctionTools, synchronizePersistedFunctions} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js";

const tool = {spec: {name: "ha_reference_id"}, function: {type: "ha_llm", tool_name: "native_tool", source_id: "source", api_id: "assist"}, enabled: true};
const panel = {_e: value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;"), _haCatalog: {saved: {ha_reference_id: {available: false, source: "source <unsafe>", description: "Unavailable"}}}};
assert.equal(isHALlmTool(tool), true);
assert.equal(haToolName(tool), "native_tool");
const card = renderHAToolCard(panel, tool, 0);
assert.match(card, /native_tool/);
assert.match(card, /Unavailable in preview/);
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

for (const transition of ["late success", "late failure", "assistant round trip"]) {
  const requests = [];
  const rendered = [];
  const errors = [];
  let host, refresh, button;
  const catalogPanel = {
    _draft: {functions: [tool]},
    _selectedAgent() { return {entry_id: `entry-${this._agentId}`}; },
    shadowRoot: {querySelector: selector => {
      if (selector === ".tools-surface") return host;
      if (selector === "#refresh-ha-tools") return button;
      return null;
    }},
    _call(section, action) {
      assert.deepEqual([section, action], ["tools", "ha_catalog"]);
      const agentId = this._agentId;
      return new Promise((resolve, reject) => requests.push({agentId, resolve, reject}));
    },
    _render() { rendered.push(this._haCatalogAgent); },
    _toast(message) { errors.push(message); },
  };
  const select = agentId => {
    catalogPanel._agentId = agentId;
    host = {};
    button = {
      isConnected: true,
      addEventListener(_event, listener) { refresh = () => listener({currentTarget: this}); },
    };
    bindHALlmTools(catalogPanel);
    return catalogPanel._haCatalogLoad.promise;
  };

  const first = select("A");
  assert.equal(select("A"), first, "rebinding the current assistant shares its pending read");
  const second = select("B");
  assert.deepEqual(requests.map(request => request.agentId), ["A", "B"]);
  const roundTrip = transition === "assistant round trip";
  const latest = roundTrip ? select("A") : second;
  const refreshResult = refresh();
  assert.equal(button.disabled, true);
  assert.deepEqual(requests.map(request => request.agentId), roundTrip ? ["A", "B", "A"] : ["A", "B"]);

  if (transition === "late failure") requests[0].reject(new Error("Old catalogue failed"));
  else requests[0].resolve({saved: {old: {}}});
  assert.equal(await first, null);
  if (roundTrip) {
    requests[1].resolve({saved: {other: {}}});
    assert.equal(await second, null);
  }
  assert.equal(catalogPanel._haCatalogLoad.promise, latest);
  assert.equal(catalogPanel._haCatalog, undefined);
  assert.deepEqual(rendered, []);
  assert.deepEqual(errors, []);

  const currentCatalog = {saved: {current: {}}};
  requests.at(-1).resolve(currentCatalog);
  await refreshResult;
  assert.equal(button.disabled, false);
  assert.equal(catalogPanel._haCatalog, currentCatalog);
  assert.equal(catalogPanel._haCatalogAgent, roundTrip ? "A" : "B");
  assert.ok(rendered.includes(catalogPanel._haCatalogAgent));
}
