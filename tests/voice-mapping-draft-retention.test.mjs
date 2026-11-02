import assert from "node:assert/strict";
import {fixture, saveConfiguration, discardConfigurationDraft, retainPanelDrafts, restorePanelDrafts, stopDraftRecovery, captureConfigurationSubmission, reconcileConfigurationAcknowledgement} from "./management-ownership-harness.mjs";
import {voiceMappingDraft, updateVoiceMappingRows, voiceMappingDraftError, voiceMappingDraftDirty, captureVoiceMappingDraft} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

const mappings={office:"user:owner",living:"shared:household"};
function setup() {
  const f=fixture();
  for(const record of f.state.records.values()) record.config.voice_device_mappings=structuredClone(mappings);
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
console.log("Voice invalid row retention, central save guard, recovery, discard and acknowledgement tests passed");
