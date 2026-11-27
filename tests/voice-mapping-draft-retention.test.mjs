import assert from "node:assert/strict";
import {fixture, saveConfiguration, discardConfigurationDraft, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery, captureConfigurationSubmission, reconcileConfigurationAcknowledgement} from "./management-ownership-harness.mjs";
import {voiceMappingDraft, updateVoiceMappingRows, voiceMappingDraftError, voiceMappingDraftDirty, captureVoiceMappingDraft, rememberVoiceDeviceAliases} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

const mappings={office:"user:owner",living:"shared:household"};
function setup() {
  const f=fixture();
  for(const record of f.state.records.values()) Object.assign(record.config, {
    voice_scope_policy:"device_mapping", voice_device_mappings:structuredClone(mappings),
  });
  return {...f,panel:f.create()};
}
for (const failure of ["duplicate","unresolved","pending","empty"]) {
  const f=setup(), panel=f.panel;
  const rows=structuredClone(voiceMappingDraft(panel).rows);
  if(failure==="duplicate") rows[0].deviceId="living";
  else if(failure==="empty") rows.push({id:"new-empty",deviceId:"",owner:"unretained",satellite:"",lookup:""});
  else Object.assign(rows[0],{lookup:failure,satellite:"assist_satellite.unavailable"});
  updateVoiceMappingRows(panel,rows);panel._syncConfigDirty();
  assert.equal(panel._configDirty,true,`${failure} remains dirty although the map is unchanged`);
  assert.deepEqual(panel._draft.voice_device_mappings,mappings);
  assert.ok(voiceMappingDraftError(panel));
  panel._subsection="basics";
  await saveConfiguration(panel,{});
  assert.equal(f.state.writes.length,0,"global Save cannot commit an invalid Voice draft off route");
  panel.isConnected=false;retainPanelDrafts(panel);
  const replacement=f.create();replacement._subsection="voice";
  restorePanelDrafts(replacement);
  assert.equal(replacement._configDirty,true,"replacement owns the complete invalid row state");
  assert.deepEqual(captureVoiceMappingDraft(replacement),rows.map(({deviceId,owner,satellite,lookup})=>({deviceId,owner,...(lookup?{satellite,lookup}:{})})));
  assert.ok(voiceMappingDraftError(replacement));
  discardConfigurationDraft(replacement);replacement._syncConfigDirty();
  assert.equal(voiceMappingDraftError(replacement),"");assert.equal(replacement._configDirty,false);
  assert.deepEqual(replacement._draft.voice_device_mappings,mappings);
  stopDraftRecovery(replacement);
}
{
  const {panel}=setup(), original=voiceMappingDraft(panel).rows;
  updateVoiceMappingRows(panel,original.map((row,i)=>({...row,owner:i?row.owner:"unretained"})));
  const submitted=captureConfigurationSubmission(panel);
  const newer=voiceMappingDraft(panel).rows.map((row,i)=>({...row,...(!i?{deviceId:"living"}:{})}));
  updateVoiceMappingRows(panel,newer);
  const saved={valid:true,title:panel._draftTitle,revision:"r1",config:structuredClone(submitted.draft)};
  reconcileConfigurationAcknowledgement(panel,saved,submitted,{fields:["voice_device_mappings"]});
  assert.ok(voiceMappingDraftError(panel),"acknowledgement preserves a newer invalid edit");
  assert.equal(voiceMappingDraftDirty(panel),true);assert.equal(panel._configDirty,true);
  panel._clearConfigDraft();
  assert.equal(voiceMappingDraftDirty(panel),false);
  assert.equal([...panel._unsavedState.scopes.keys()].some(key=>key.startsWith("voice-mapping-rows:")),false);
}
{
  const {state,panel}=setup();
  const rows=[...voiceMappingDraft(panel).rows,{id:"unfinished",deviceId:"",owner:"unretained",satellite:"",lookup:""}];
  updateVoiceMappingRows(panel,rows);
  panel._draft.voice_scope_policy="shared";
  panel._syncConfigDirty();
  await saveConfiguration(panel,{});
  assert.deepEqual(state.writes[0].config,{voice_scope_policy:"shared"});
  assert.deepEqual(panel._configData.config.voice_device_mappings,mappings);
  assert.equal(captureVoiceMappingDraft(panel).length,3,"a full response to a different field cannot consume unfinished rows");
  assert.ok(voiceMappingDraftError(panel));
}
{
  const f=setup();
  const legacy="assist_satellite.kitchen", canonical="registry-kitchen";
  for (const record of f.state.records.values()) {
    record.config.voice_device_mappings = {[legacy]:"user:owner", [canonical]:"unretained"};
  }
  const panel=f.create();
  const rows=structuredClone(voiceMappingDraft(panel).rows);
  rememberVoiceDeviceAliases(panel,[{entity_id:legacy, device_id:canonical}]);
  assert.match(voiceMappingDraftError(panel), /assigned more than once/);
  const [old, current]=rows;
  assert.match(updateVoiceMappingRows(panel,[old]).error, /reactivate/);
  assert.deepEqual(panel._draft.voice_device_mappings, {[legacy]:"user:owner", [canonical]:"unretained"});
  assert.deepEqual(updateVoiceMappingRows(panel,[current]).mapping, {[canonical]:"unretained"});
  assert.deepEqual(updateVoiceMappingRows(panel,[{...old,deviceId:canonical}]).mapping, {[canonical]:"user:owner"});
  assert.deepEqual(updateVoiceMappingRows(panel,[]).mapping, {});
}

for (const freshAssociation of [false, true]) {
  const f=setup();
  const legacy="assist_satellite.kitchen", canonical="registry-kitchen";
  for (const record of f.state.records.values()) {
    record.config.voice_device_mappings = {[legacy]:"user:owner"};
  }
  const panel=f.create();
  rememberVoiceDeviceAliases(panel,[{entity_id:legacy, device_id:canonical}]);
  const rows=[...voiceMappingDraft(panel).rows,{id:"alias-row",deviceId:canonical,owner:"unretained"}];
  assert.match(updateVoiceMappingRows(panel,rows).error, /assigned more than once/);
  panel._syncConfigDirty();
  panel.isConnected=false;
  retainPanelDrafts(panel);
  const replacement=f.create();
  replacement._subsection="basics";
  if (freshAssociation) {
    rememberVoiceDeviceAliases(replacement,[{entity_id:legacy, device_id:"different-device"}]);
  }
  restorePanelDrafts(replacement);
  if (freshAssociation) {
    assert.equal(voiceMappingDraftError(replacement), "");
    assert.deepEqual(replacement._draft.voice_device_mappings, {[legacy]:"user:owner", [canonical]:"unretained"});
  } else {
    assert.match(voiceMappingDraftError(replacement), /assigned more than once/);
    await saveConfiguration(replacement,{});
    assert.equal(f.state.writes.length, 0, "off-route Save must preserve the recovered alias conflict");
  }
  stopDraftRecovery(replacement);
}

console.log("Voice invalid row retention, central save guard, recovery, discard and acknowledgement tests passed");
