import assert from "node:assert/strict";
import {bindFallbackRepair} from "../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

async function repairScenario({refresh, reject = false}) {
  let click;
  const button = {dataset:{}, disabled:false, addEventListener:(_type, listener) => { click = listener; }};
  const repaired = {spec:{name:"repaired"}};
  const saved = {valid:true, revision:"acknowledged", tools:[repaired], config:{functions:[repaired], function_groups:[]}};
  const calls = [], toasts = [];
  let finish;
  const completed = new Promise(resolve => { finish = resolve; });
  const panel = {
    _agentId:"agent-a", _draftAgentId:"agent-a",
    _configData:{revision:"old", title:"Saved title", config:{prompt:"Saved prompt", functions:[], function_groups:[]}},
    _draft:{prompt:"Newer unsaved prompt", functions:[], function_groups:[]}, _draftTitle:"Unsaved title",
    _result:{revision:"old"},
    shadowRoot:{querySelector:selector => selector === "#function-repair-save" ? button : {value:JSON.stringify([repaired])}},
    _call:async (section, action) => {
      calls.push([section, action]);
      assert.equal(section, "function_repair", "the acknowledgement must not require a configuration reread");
      assert.equal(action, "save");
      if (reject) throw new Error("Write rejected");
      return saved;
    },
    _rememberCleanConfiguration(data) { this.clean = structuredClone(data); },
    _syncConfigDirty() {},
    _loadAgents:async () => {
      assert.equal(panel._configData.revision, "acknowledged", "install acknowledgement before refreshing");
      if (refresh === "throws") throw new Error("Catalogue unavailable");
      if (refresh === "handled") panel._error = "Catalogue unavailable";
    },
    _toast:(...args) => { toasts.push(args); finish(); },
  };
  bindFallbackRepair(panel);
  click();
  await completed;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(button.disabled, false);
  assert.equal(panel._draft.prompt, "Newer unsaved prompt");
  assert.equal(panel._draftTitle, "Unsaved title");
  assert.deepEqual(calls, [["function_repair", "save"]]);
  if (reject) {
    assert.equal(panel._configData.revision, "old");
    assert.deepEqual(panel._draft.functions, []);
    assert.match(toasts[0][0], /^Unable to repair Function Tools: Write rejected$/);
  } else {
    assert.equal(panel._configData.revision, "acknowledged");
    assert.equal(panel.clean.revision, "acknowledged");
    assert.deepEqual(panel._draft.functions, [repaired]);
    assert.equal(panel._configData.config.prompt, "Saved prompt");
    assert.equal(panel._result, panel._configData);
    assert.match(toasts[0][0], /^Function Tools repaired/);
    if (refresh) assert.match(toasts[0][0], /but .*could not be refreshed: Catalogue unavailable/);
    else assert.equal(toasts[0][0], "Function Tools repaired");
    assert.ok(!toasts.some(([message]) => message.startsWith("Unable to repair")));
  }
}

await repairScenario({});
await repairScenario({refresh:"throws"});
await repairScenario({refresh:"handled"});
await repairScenario({reject:true});
