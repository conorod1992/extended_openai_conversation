import assert from "node:assert/strict";
import test from "node:test";
import {ExtendedOpenAIManagementPanel, fixture, flushUntil, retire} from "./management-ownership-harness.mjs";

test("an accepted empty cold catalogue finishes loading without automatic retries", async () => {
  const {create} = fixture();
  const panel = create();
  Object.assign(panel, {_data:null, _agentId:null, _draftAgentId:null,
    _configData:null, _draft:null, _eocCatalogBootstrap:null});
  let reads = 0;
  panel._hass.callWS = async request => {
    assert.equal(request.action, "agents");
    reads++;
    return {agents:[], is_admin:true, scopes:[]};
  };
  // Keep the actual accepted catalogue path and no-selected-agent route body;
  // lazy presentation assets are unrelated to this state-completion contract.
  panel._loadSection = () => ExtendedOpenAIManagementPanel.prototype._loadSectionData.call(panel);
  try {
    panel._loadInitialAgents();
    await flushUntil(() => !panel._eocCatalogBootstrap.pending);
    assert.deepEqual(panel._data.agents, []);
    assert.equal(panel._error, null);
    assert.equal(panel._busy, false, "an empty successful read is a completed state");
    panel.hass = {...panel.hass};
    panel.hass = {...panel.hass};
    assert.equal(reads, 1, "ordinary HA updates do not repeat a completed empty read");
  } finally { retire(panel); }
});
