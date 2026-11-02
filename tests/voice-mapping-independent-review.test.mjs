import assert from "node:assert/strict";
import {bindVoiceMappings} from "../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js";
import {voiceMappingDraft, voiceMappingDraftError} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";
import {captureConfigurationSubmission, reconcileConfigurationAcknowledgement} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

function liveVoiceRow() {
  const calls = [], lookups = [];
  const picker = new TestEventTarget();
  picker.dataset = {};
  const hidden = {value:"device-office"};
  const retainedOwner = {value:"user:owner"};
  const card = new TestEventTarget();
  const row = {dataset:{}, isConnected:true, querySelector(selector) {
    return {".voice-satellite-picker":picker, ".voice-device-id":hidden,
      ".voice-mapping-owner":retainedOwner, ".voice-satellite-warning":{}}[selector] || null;
  }};
  const config = {prompt:"Saved", voice_scope_policy:"device_mapping",
    voice_device_mappings:{"device-office":"user:owner"}};
  const panel = {_agentId:"voice", _draftAgentId:"voice", _draftTitle:"Voice",
    _selectedAgent:() => ({entry_id:"provider"}), _viewKey:() => "assistant/voice",
    _configData:{config:structuredClone(config), title:"Voice", revision:"r0"},
    _draft:structuredClone(config), hass:{user:{id:"admin"}, callWS(message) {
      calls.push(message);
      if (message.type === "config/entity_registry/list") return Promise.resolve([
        {entity_id:"assist_satellite.office", device_id:"device-office"},
      ]);
      const lookup = deferred(); lookups.push(lookup); return lookup.promise;
    }}, shadowRoot:{querySelector(selector) {
      return selector === "#voice-mappings" ? card : null;
    }, querySelectorAll(selector) {return selector === "[data-voice-mapping-row]" ? [row] : []; }},
  };
  row.dataset.voiceRowId = voiceMappingDraft(panel).rows[0].id;
  bindVoiceMappings(panel);
  const select = async entity => {
    picker.value = entity;
    await Promise.all([...picker.listeners.get("value-changed") || []]
      .map(listener => listener({detail:{value:entity}})));
  };
  return {panel, row, picker, hidden, calls, lookups, select};
}

// The successful save path preserves Voice's native widgets. Their row identity
// must therefore survive acknowledgement, allowing another picker edit in place.
{
  const h = liveVoiceRow();
  await Promise.resolve(); await Promise.resolve();
  h.panel._draft.prompt = "Submitted";
  const submission = captureConfigurationSubmission(h.panel);
  reconcileConfigurationAcknowledgement(h.panel, {valid:true, title:"Voice", revision:"r1",
    config:structuredClone(submission.draft)}, submission, {fields:["voice_device_mappings", "prompt"]});
  const selected = h.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.lookups.length, 1, "acknowledgement keeps the live satellite picker active");
  h.lookups[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"device-kitchen"});
  await selected;
  assert.deepEqual(h.panel._draft.voice_device_mappings, {"device-kitchen":"user:owner"});
}

// A later lookup is a new authored edit even if an earlier full save completes
// while it is pending. The acknowledgement cannot consume or unblock that edit.
{
  const h = liveVoiceRow();
  await Promise.resolve(); await Promise.resolve();
  const submission = captureConfigurationSubmission(h.panel);
  const selected = h.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  reconcileConfigurationAcknowledgement(h.panel, {valid:true, title:"Voice", revision:"r1",
    config:structuredClone(submission.draft)}, submission);
  assert.match(voiceMappingDraftError(h.panel), /Wait/);
  assert.equal(h.row.dataset.voiceRowId, voiceMappingDraft(h.panel).rows[0].id);
  h.lookups[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"device-kitchen"});
  await selected;
  assert.equal(voiceMappingDraftError(h.panel), "");
  assert.deepEqual(h.panel._draft.voice_device_mappings, {"device-kitchen":"user:owner"});
}

// Returning to the same user does not authorize a lookup admitted in an older
// authentication generation to change the draft or retained personal scope.
{
  const h = liveVoiceRow();
  await Promise.resolve(); await Promise.resolve();
  const selected = h.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.panel._eocAuthenticationGeneration = 2;
  h.lookups[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"stale-device"});
  await selected;
  assert.equal(h.hidden.value, "device-office");
  assert.deepEqual(h.panel._draft.voice_device_mappings, {"device-office":"user:owner"});
}

// Native rows remain mounted across an authentication interruption. New edits
// by the returning user capture fresh authority, while earlier work stays stale.
{
  const h = liveVoiceRow();
  await Promise.resolve(); await Promise.resolve();
  const first = h.select("assist_satellite.kitchen");
  await Promise.resolve(); await Promise.resolve();
  h.lookups[0].resolve({entity_id:"assist_satellite.kitchen", device_id:"device-kitchen"});
  await first;
  assert.equal(h.hidden.value, "device-kitchen");
  const stale = h.select("assist_satellite.office");
  await Promise.resolve(); await Promise.resolve();
  h.panel.hass.user = null;
  h.panel._eocAuthenticationGeneration = 1;
  h.panel.hass.user = {id:"admin"};
  h.panel._eocAuthenticationGeneration = 2;
  const fresh = h.select("assist_satellite.hall");
  await Promise.resolve(); await Promise.resolve();
  assert.equal(h.lookups.length, 3, "a returning user's new selection reaches the registry");
  h.lookups[2].resolve({entity_id:"assist_satellite.hall", device_id:"device-hall"});
  await fresh;
  assert.equal(h.hidden.value, "device-hall");
  h.lookups[1].resolve({entity_id:"assist_satellite.office", device_id:"stale-device"});
  await stale;
  assert.equal(h.hidden.value, "device-hall");
  assert.deepEqual(h.panel._draft.voice_device_mappings, {"device-hall":"user:owner"});
}
console.log("Independent Voice acknowledgement, picker continuation and lookup ownership contracts passed");
