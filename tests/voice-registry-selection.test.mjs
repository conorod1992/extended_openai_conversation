import assert from "node:assert/strict";
import {bindVoiceMappings} from "../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

function harness(firstFails = false, hydration = null) {
  const ready = deferred();
  const picker = new TestEventTarget();
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
  const panel = {_agentId:"voice-agent", _viewKey:() => "assistant/voice", _draft:{voice_scope_policy:"device_mapping"},
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
  assert.equal(typeof JSON.parse(h.card.value), "string", "pending lookup cannot submit an old association");
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
  assert.equal(typeof JSON.parse(h.card.value), "string");
  assert.match(h.error.textContent, /available/);
  const retry = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[1].resolve({entity_id:"assist_satellite.kitchen", device_id:"retried-device"});
  await retry;
  assert.deepEqual(JSON.parse(h.card.value), {"retried-device":"user:office-owner"});
}

// Missing or mismatched entries must block saving the previously committed device.
for (const entry of [null, {}, {entity_id:"assist_satellite.kitchen", device_id:null},
    {entity_id:"assist_satellite.office", device_id:"wrong-device"}]) {
  const h = harness(); await h.ready;
  const selected = h.picker.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.responses[0].resolve(entry); await selected;
  assert.equal(h.hidden.value, "device-office");
  assert.equal(typeof JSON.parse(h.card.value), "string");
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
  assert.deepEqual(JSON.parse(h.card.value), {});
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
console.log("Voice registry freshness, retry and selection ownership tests passed");
