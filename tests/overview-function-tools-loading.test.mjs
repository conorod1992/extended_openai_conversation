import assert from "node:assert/strict";
import {startOverviewDetailReads} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page.js";
import {buildSetupHealth} from "../custom_components/extended_openai_conversation_responses/frontend/overview-health.js";

const cases = [
  [{usable_count:3, enabled_count:3, invalid_count:0}, "3 functions available"],
  [{usable_count:0, enabled_count:0, invalid_count:0}, "0 functions available"],
  [{usable_count:3, invalid_count:1, isolatable:true, validation_error:"Invalid tool"}, "1 function needs repair"],
  [{usable_count:0, invalid_count:0, isolatable:false, validation_error:"Invalid YAML"}, "Configuration needs repair"],
];

function panelFor(functionTools) {
  const agent = {entry_id:"entry-a", subentry_id:"agent-a"};
  const pending = new Map();
  const panel = {
    _agentId:agent.subentry_id, _loadToken:1, _cacheGeneration:1,
    _viewKey:()=> "overview", _selectedAgent:()=> agent, _render() {},
    _result:{setup_health:{function_tools:functionTools, memory:{loading:true}}},
    _hass:{callWS:({kind})=> new Promise((resolve, reject)=> pending.set(kind, {resolve, reject}))},
  };
  return {panel, pending};
}

for (const [health, expected] of cases) {
  for (const initial of [{loading:true}, {unavailable:true, loading:false}]) {
    const {panel, pending} = panelFor(initial);
    const reads = startOverviewDetailReads(panel);
    pending.get("setup_health").resolve({setup_health:{function_tools:health}});
    await Promise.resolve();
    assert.equal(panel._result.loading.setup_health, false);
    const card = buildSetupHealth(panel._result.setup_health).checks.find(check=>check.id === "function_tools");
    assert.equal(card.value, expected, "completed health replaces the pending or failed card immediately");
    assert.equal(panel._result.setup_health.memory.loading, true, "unrelated pending facts are preserved");
    for (const [kind, read] of pending) if (kind !== "setup_health") read.resolve({});
    await reads;
  }
}

{
  const {panel, pending} = panelFor({loading:true});
  const reads = startOverviewDetailReads(panel);
  pending.get("setup_health").reject(new Error("Connection lost"));
  await Promise.resolve();
  const card = buildSetupHealth(panel._result.setup_health).checks.find(check=>check.id === "function_tools");
  assert.equal(card.value, "Unable to determine");
  assert.equal(panel._result.setup_health.function_tools.loading, false);
  for (const [kind, read] of pending) if (kind !== "setup_health") read.resolve({});
  await reads;
}

console.log("Overview Function Tools loading regressions passed.");
