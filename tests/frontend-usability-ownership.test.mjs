import assert from "node:assert/strict";
import test from "node:test";
import {saveConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/management-actions.js";
import {captureGuestInterval, acknowledgeGuestInterval} from "../custom_components/extended_openai_conversation_responses/frontend/management-guest-feature.js";

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return {promise, resolve}; };
function owner() {
  return {_agentId:"assistant", _draftAgentId:"assistant", _hass:{user:{id:"owner"}}, get hass() { return this._hass; },
    _selectedAgent:() => ({entry_id:"entry", subentry_id:"assistant"}), _viewKey:() => "assistant/basics",
    _configData:{revision:"r1", title:"Assistant", config:{usage_request_retention_days:30, prompt:"saved"}},
    _draft:{usage_request_retention_days:0, prompt:"draft"}, _draftTitle:"Assistant", _configDirty:true,
    _eocDirtyConfigKeys:new Set(["usage_request_retention_days", "prompt"]),
    shadowRoot:{querySelector:() => null}, _setSaving() {}, _toast() {}};
}

for (const change of ["cancel", "assistant", "authentication", "draft"]) {
  test(`retention confirmation cannot dispatch after ${change}`, async () => {
    const panel = owner(), held = deferred();
    let writes = 0;
    panel._confirm = () => held.promise;
    panel._call = () => { writes++; throw Error("Write must not be admitted"); };
    const pending = saveConfiguration(panel, null);
    assert.equal(panel._configurationSaving, true);
    if (change === "assistant") panel._agentId = "another";
    if (change === "authentication") panel._eocAuthenticationGeneration = 2;
    if (change === "draft") panel._draft.prompt = "newer";
    held.resolve(change !== "cancel");
    await pending;
    assert.equal(writes, 0);
    assert.equal(panel._draft.usage_request_retention_days, 0);
    assert.equal(panel._draft.prompt, change === "draft" ? "newer" : "draft");
    assert.equal(panel._configurationSaving, false);
  });
}

test("only a submitted positive-to-disabled retention change requires confirmation", async () => {
  for (const [saved, draft, confirmed] of [[30,0,true], [0,0,false], [30,90,false], [30,7,false]]) {
    const panel = owner();
    panel._configData.config.usage_request_retention_days = saved;
    panel._draft.usage_request_retention_days = draft;
    const confirmations = [], writes = [];
    panel._confirm = async (...args) => { confirmations.push(args); return true; };
    panel._call = async (...args) => { writes.push(args); throw Error("Controlled failed save"); };
    await saveConfiguration(panel, null);
    assert.equal(confirmations.length, Number(confirmed));
    assert.equal(writes.length, 1);
    assert.equal(panel._draft.prompt, "draft");
    if (confirmed) assert.match(confirmations[0][1], /request details.*Overall usage totals/);
  }
});

test("Guest interval acknowledgements preserve newer fields and cannot publish across owners", () => {
  const panel = owner();
  panel._result = {status:{active_from:"saved-start", active_until:"saved-end", indefinite:false}};
  panel._dateTimeLocal = value => value || "";
  const inputs = {"#guest-start":{value:"authored-start"}, "#guest-end":{value:"authored-end"}, "#guest-indefinite":{checked:true}};
  panel.shadowRoot.querySelector = selector => inputs[selector] || null;
  const submitted = captureGuestInterval(panel);
  acknowledgeGuestInterval(panel, submitted, {state:"active"});
  assert.deepEqual(panel._guestIntervalDraft.values, submitted.values);
  inputs["#guest-start"].value = "newer-start";
  captureGuestInterval(panel);
  acknowledgeGuestInterval(panel, submitted, {active_from:"canonical-start", active_until:null, indefinite:true});
  assert.deepEqual(panel._guestIntervalDraft.values, {start:"newer-start", end:"authored-end", indefinite:true});
  panel._hass.user.id = "different-user";
  assert.equal(captureGuestInterval(panel), null);
  acknowledgeGuestInterval(panel, submitted, {active_from:"foreign-start", indefinite:false});
  assert.equal(panel._guestIntervalDraft.values.start, "newer-start");
  panel._hass.user.id = "owner";
  assert.equal(panel._unsavedState.scopes.get("guest-interval").dirty(), true);
  panel._unsavedState.scopes.get("guest-interval").discard();
  assert.deepEqual(panel._guestIntervalDraft.values, panel._guestIntervalDraft.baseline);
});
