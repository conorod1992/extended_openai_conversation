import assert from "node:assert/strict";
import {bindVoiceMappings} from "../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js";
import {voiceMappingDraft, voiceMappingDraftError} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

function harness(firstFails = false, hydration = null) {
  const ready = deferred();
  const picker = new TestEventTarget(); picker.dataset={};
  const addListener = picker.addEventListener.bind(picker);
  picker.addEventListener = (type, listener) => {addListener(type, listener); ready.resolve();};
  picker.select = value => {
    picker.value = value;
    return Promise.all([...picker.listeners.get("value-changed") || []].map(fn => fn({detail:{value}})));
  };
  const hidden = {value:"device-office"}, owner = {value:"user:office-owner"}, warning = {};
  const row = {dataset:{}, isConnected:true, querySelector(selector) {
    return {".voice-satellite-picker":picker, ".voice-device-id":hidden, ".voice-mapping-owner":owner,
      ".voice-satellite-warning":warning}[selector] || null;
  }};
  const card = new TestEventTarget();
  const error = {};
  const calls = [], responses = [];
  const panel = {_agentId:"voice-agent", _viewKey:() => "assistant/voice", _draft:{voice_scope_policy:"device_mapping", voice_device_mappings:{"device-office":"user:office-owner"}},
    hass:{callWS(message) {
      calls.push(message);
      assert.equal(message.type, calls.length === 1 ? "config/entity_registry/list" : "config/entity_registry/get");
      if (calls.length === 1 && hydration) return hydration.promise;
      if (calls.length === 1) return firstFails ? Promise.reject(new Error("Registry offline")) : Promise.resolve([
        {entity_id:"assist_satellite.kitchen", device_id:"device-office"},
      ]);
      const response = deferred(); responses.push(response); return response.promise;
    }},
    shadowRoot:{querySelector(selector) {return selector === "#voice-mappings" ? card
      : selector === '[data-error="voice_device_mappings"]' ? error : null;},
      querySelectorAll(selector) {return selector === "[data-voice-mapping-row]" ? [row] : [];},
    },
  };
  row.dataset.voiceRowId=voiceMappingDraft(panel).rows[0].id;
  bindVoiceMappings(panel);
  return {panel, picker, hidden, owner, warning, card, error, calls, responses, ready:ready.promise};
}

for (const fails of [false, true]) {
  const h = harness(fails);
  await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.calls.length, 2, "selection fetches again after warm-up or outage");
  assert.deepEqual(h.calls[1], {type:"config/entity_registry/get", entity_id:"assist_satellite.kitchen"});
  assert.deepEqual(JSON.parse(h.card.value), {"device-office":"user:office-owner"});
  assert.match(voiceMappingDraftError(h.panel), /Wait/);
  assert.equal(h.hidden.value, "device-office", "the committed value is retained while lookup settles");
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"device-replacement"});
  await selected;
  assert.equal(h.hidden.value, "device-replacement");
  assert.deepEqual(JSON.parse(h.card.value), {"device-replacement":"user:office-owner"});
}

// A user can select before initial hydration settles. Its later snapshot must
// neither lose that interaction nor overwrite the resulting association.
{
  const hydration = deferred();
  const h = harness(false, hydration); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.calls.length, 2);
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"selected-device"});
  await selected;
  hydration.resolve([{entity_id:"assist_satellite.office", device_id:"device-office"}]);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.picker.value, "assist_satellite.kitchen");
  assert.equal(h.hidden.value, "selected-device");
}

// A later selection owns the row even when an older fetch completes last.
{
  const h = harness(); await h.ready;
  const first = h.picker.select("assist_satellite.kitchen");
  const second = h.picker.select("assist_satellite.office");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.calls.length, 3);
  assert.deepEqual(h.calls.slice(1), [
    {type:"config/entity_registry/get", entity_id:"assist_satellite.kitchen"},
    {type:"config/entity_registry/get", entity_id:"assist_satellite.office"},
  ]);
  h.responses[1].resolve({entity_id:"assist_satellite.office", device_id:"device-office-now"});
  await second;
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"stale-device"});
  await first;
  assert.equal(h.hidden.value, "device-office-now");
  assert.deepEqual(JSON.parse(h.card.value), {"device-office-now":"user:office-owner"});
}

// Failed lookups cannot silently submit the previously saved device; retry recovers.
{
  const h = harness(); await h.ready;
  const failed = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[0].reject(new Error("Registry unavailable")); await failed;
  assert.deepEqual(JSON.parse(h.card.value), {"device-office":"user:office-owner"});
  assert.match(voiceMappingDraftError(h.panel), /available/);
  assert.match(h.error.textContent, /available/);
  const retry = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[1].resolve({entity_id:"assist_satellite.kitchen", device_id:"retried-device"});
  await retry;
  assert.deepEqual(JSON.parse(h.card.value), {"retried-device":"user:office-owner"});
}

// A registered satellite with no associated device is saved by entity ID.
{
  const h = harness(); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:null});
  await selected;
  assert.deepEqual(JSON.parse(h.card.value), {"assist_satellite.kitchen":"user:office-owner"});
  assert.equal(voiceMappingDraftError(h.panel), "");
}

// A missing or mismatched entry without a live satellite remains unresolved.
for (const entry of [null, {}, {entity_id:"assist_satellite.office", device_id:"wrong-device"}]) {
  const h = harness(); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[0].resolve(entry); await selected;
  assert.equal(h.hidden.value, "device-office");
  assert.deepEqual(JSON.parse(h.card.value), {"device-office":"user:office-owner"});
  assert.match(voiceMappingDraftError(h.panel), /available/);
  assert.match(h.error.textContent, /available/);
}

// Clearing a selection owns the row even if its earlier request finishes later.
{
  const h = harness(); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  await h.picker.select("");
  assert.equal(h.calls.length, 2, "clearing requires no registry lookup");
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"stale-device"});
  await selected;
  assert.equal(h.hidden.value, "");
  assert.deepEqual(JSON.parse(h.card.value), {"device-office":"user:office-owner"});
  assert.match(voiceMappingDraftError(h.panel), /empty assignment/);
}

// A response cannot change a detached row or a panel that has changed ownership.
for (const changeOwner of [h => {h.panel._agentId = "other-agent";},
    h => {h.panel._viewKey = () => "assistant/basics";},
    h => {h.panel.shadowRoot.querySelectorAll("[data-voice-mapping-row]")[0].isConnected = false;}]) {
  const h = harness(); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  changeOwner(h);
  h.responses[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"stale-device"});
  await selected;
  assert.equal(h.hidden.value, "device-office");
}
for (const action of [
  "keep-legacy-owner", "remove-effective", "fresh-alias", "remove-duplicate",
  "remove-duplicate-with-legacy", "remove-unsaved-canonical",
]) {
  const legacy = "assist_satellite.kitchen", device = "registry-kitchen";
  const mapping = action === "remove-duplicate" ? {[device]:"user:person"} :
    action === "remove-unsaved-canonical" ? {[legacy]:"user:person"} :
    {[legacy]:"user:person", [device]:"unretained"};
  const removeNew = action.startsWith("remove-duplicate") || action === "remove-unsaved-canonical";
  const card = new TestEventTarget(), error = {};
  const hydration = deferred();
  const panel = {
    _agentId:"voice-agent", _viewKey:()=>"assistant/voice",
    _draft:{voice_device_mappings:{...mapping}},
    _configData:{config:{voice_device_mappings:{...mapping}}},
    hass:{callWS:async message => {
      if (message.type === "config/entity_registry/get") return {entity_id:legacy, device_id:device};
      if (action === "fresh-alias") return hydration.promise;
      return [{entity_id:legacy, device_id:device}];
    }},
  };
  const draft = voiceMappingDraft(panel);
  if (removeNew) {
    draft.rows.push({id:"duplicate-row", deviceId:device, owner:"unretained"});
  }
  const rows = draft.rows.map(saved => {
    const picker = new TestEventTarget(); picker.dataset={};
    const remove = new TestEventTarget(), keep = new TestEventTarget();
    const nodes = {".voice-satellite-picker":picker, ".voice-device-id":{value:saved.deviceId},
      ".voice-mapping-owner":{value:saved.owner}, ".remove-voice-mapping":remove,
      ".keep-voice-mapping":keep, ".voice-satellite-warning":{}};
    return {
      isConnected:true, dataset:{voiceRowId:saved.id}, picker, removeButton:remove, keep,
      querySelector: selector => nodes[selector] || null,
      remove() { this.isConnected = false; },
    };
  });
  const visible = () => rows.filter(row => row.isConnected);
  panel.shadowRoot = {
    querySelector: selector => ({
      "#voice-mappings":card, "#voice-mapping-list":{},
      '[data-error="voice_device_mappings"]':error,
      "[data-voice-mapping-row]":visible()[0],
    })[selector] || null,
    querySelectorAll: selector => selector === "[data-voice-mapping-row]" ? visible() : [],
  };
  bindVoiceMappings(panel);
  if (action === "fresh-alias") {
    await Promise.all([...rows[1].picker.listeners.get("value-changed")]
      .map(listener => listener({detail:{value:legacy}})));
    assert.match(error.textContent, /assigned more than once/);
    hydration.resolve([{entity_id:legacy, device_id:"outdated-device"}]);
  }
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(rows.every(row => row.picker.value === legacy));
  assert.match(error.textContent, /assigned more than once/);
  if (action === "keep-legacy-owner") {
    assert.equal(rows[0].keep.hidden, false);
    rows[0].keep.dispatchEvent(new Event("click"));
    assert.deepEqual(panel._draft.voice_device_mappings, {[device]:"user:person"});
  } else if (removeNew) {
    const extra = rows.at(-1);
    assert.equal(extra.removeButton.textContent, "Remove");
    extra.removeButton.dispatchEvent(new Event("click"));
    assert.deepEqual(panel._draft.voice_device_mappings, mapping);
    assert.deepEqual(visible(), rows.slice(0,-1));
  } else if (action === "remove-effective") {
    assert.match(rows[1].removeButton.textContent, /Remove all assignments/);
    rows[1].removeButton.dispatchEvent(new Event("click"));
    assert.deepEqual(panel._draft.voice_device_mappings, {});
  }
  if (action === "remove-duplicate-with-legacy") {
    assert.match(error.textContent, /assigned more than once/);
    assert.match(rows[1].removeButton.textContent, /Remove all assignments/);
  } else if (action !== "fresh-alias") assert.equal(error.textContent, "");
}

console.log("Voice registry freshness, retry and selection ownership tests passed");
