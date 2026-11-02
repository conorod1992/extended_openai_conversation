import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire, flushUntil} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";
import {captureCatalogSelectionIntent} from "../custom_components/extended_openai_conversation_responses/frontend/management-catalog-ownership.js";
import {applyConfigurationImport, refreshUncertainConfigurationImport} from "../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js";

test("editing an uncertain import cannot hide a real catalogue refresh failure", async () => {
  const {create} = fixture();
  const panel = create();
  panel._error = null;
  const source = {id:"import-document", value:"Original imported document"};
  const dialog = {querySelectorAll:() => [source], close() {}};
  panel.shadowRoot.querySelectorAll = selector => selector === "dialog[open]" ? [dialog] : [];
  const intent = captureCatalogSelectionIntent(panel);
  const recovery = {owner:intent.owner, intent};
  panel._eocImportRecovery = recovery;
  source.value = "Another imported document";
  panel._hass.callWS = async () => { throw Error("Authoritative catalogue unavailable"); };

  try {
    await assert.rejects(refreshUncertainConfigurationImport(panel), /catalogue unavailable/i);
    assert.equal(panel._eocImportRecovery, recovery, "a failed real read cannot authorize a new import preview");
  } finally {
    retire(panel);
  }
});

for (const fault of ["suppressed failure", "superseded read", "malformed response"]) {
  test(`an uncertain import cannot use ${fault} as authoritative recovery`, async () => {
    const {create, state} = fixture();
    const panel = create();
    panel._error = null;
    const source = {id:"import-document", value:"Retained imported document"};
    const dialog = {querySelectorAll:() => [source], close() {}};
    panel.shadowRoot.querySelectorAll = selector => selector === "dialog[open]" ? [dialog] : [];
    const recovery = {owner:captureCatalogSelectionIntent(panel).owner};
    panel._eocImportRecovery = recovery;
    const gate = deferred();
    const failure = Error("Retired catalogue failure");
    let entered = false;
    panel._hass.callWS = async () => {
      entered = true;
      await gate.promise;
      if (fault === "suppressed failure") throw failure;
      return {is_admin:true, agents:fault === "malformed response" ? null : structuredClone(state.agents), scopes:[]};
    };
    panel._loadSection = async () => {};
    try {
      const refresh = refreshUncertainConfigurationImport(panel);
      await flushUntil(() => entered);
      if (fault === "suppressed failure") source.value = "Authored while refreshing";
      if (fault === "superseded read") panel._eocCatalogReadSequence++;
      gate.resolve();
      await assert.rejects(refresh, fault === "suppressed failure"
        ? error => error === failure : /could not be refreshed|invalid assistant catalogue/);
      assert.equal(panel._eocImportRecovery, recovery);
      assert.equal(source.value, fault === "suppressed failure" ? "Authored while refreshing" : "Retained imported document");
    } finally { retire(panel); }
  });
}

test("an accepted real catalogue read clears only its own import recovery and preserves edited source", async () => {
  const {create, state} = fixture();
  const panel = create();
  panel._error = null;
  const source = {id:"import-document", value:"Newer reviewed source"};
  const dialog = {querySelectorAll:() => [source], close() {}};
  panel.shadowRoot.querySelectorAll = selector => selector === "dialog[open]" ? [dialog] : [];
  panel._eocImportRecovery = {owner:captureCatalogSelectionIntent(panel).owner};
  const restored = {entry_id:state.entry, subentry_id:"created-by-lost-import", title:"Created before disconnect"};
  panel._hass.callWS = async () => ({is_admin:true, agents:[...structuredClone(state.agents), restored], scopes:[]});
  panel._loadSection = async () => {};
  try {
    await refreshUncertainConfigurationImport(panel);
    assert.equal(panel._eocImportRecovery, null);
    assert.equal(source.value, "Newer reviewed source");
    assert.ok(panel._data.agents.some(agent => agent.subentry_id === restored.subentry_id));
  } finally { retire(panel); }
});

for (const transition of ["authentication round trip", "draft replacement", "assistant selection"]) {
  for (const outcome of ["lost response", "acknowledged response"]) {
    test(`a pending import's ${outcome} keeps a catalogue barrier after ${transition}`, async () => {
      const {create, state, agentB} = fixture();
      const panel = create();
      const source = {id:"import-document", value:"Reviewed imported source"};
      const apply = {disabled:false};
      const summary = {textContent:"Reviewed source"};
      const controls = new Map([
        ["#import-document", source], ["#import-apply", apply], ["#import-summary", summary],
        ['input[name="import-mode"]:checked', {value:"new"}], ["#import-dialog", {close() {}}],
      ]);
      panel.shadowRoot.querySelector = selector => controls.get(selector) || null;
      panel._importDocument = source.value;
      panel._importMode = "new";
      panel._importRevision = panel._configData.revision;
      const gate = deferred();
      let writes = 0;
      panel._hass.callWS = async request => {
        assert.equal(request.action, "import");
        writes++;
        await gate.promise;
        if (outcome === "lost response") throw Error("Transport failed after the import committed");
        return {status:"created", subentry_id:"created-before-transition"};
      };
      try {
        const pending = applyConfigurationImport(panel, panel.shadowRoot);
        await flushUntil(() => writes === 1);
        if (transition === "authentication round trip") {
          const original = panel.hass;
          panel.hass = {...original, user:{id:"intervening-user"}};
          panel.hass = original;
        } else {
          const saved = transition === "assistant selection" ? state.snapshot(agentB) : structuredClone(panel._configData);
          panel._clearConfigDraft();
          if (transition === "assistant selection") panel._agentId = agentB;
          Object.assign(panel, {_configData:saved, _draft:structuredClone(saved.config),
            _draftTitle:saved.title, _draftAgentId:panel._agentId});
        }
        const currentDraft = structuredClone(panel._draft);
        gate.resolve();
        await pending;
        assert.deepEqual(panel._eocImportRecovery, {}, "retiring the caller cannot certify the catalogue after its write");
        assert.deepEqual(panel._draft, currentDraft, "old completion cannot mutate a newer draft");
        assert.equal(source.value, "Reviewed imported source");
        assert.equal(panel._importDocument, source.value, "old completion cannot retire a later owner’s preview");
        assert.equal(apply.disabled, true, "a retained preview cannot offer Apply while its catalogue is uncertain");
        assert.deepEqual(panel.toasts, [], "retired completions cannot publish their notifications");
        await applyConfigurationImport(panel, panel.shadowRoot);
        assert.equal(writes, 1, "Apply requires another owned preview and accepted catalogue read");
      } finally { retire(panel); }
    });
  }
}
