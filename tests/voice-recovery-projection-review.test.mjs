import assert from "node:assert/strict";
import {renderVoiceIdentityCore} from "../custom_components/extended_openai_conversation_responses/frontend/voice-identity-core.js";
import {voiceMappingDraft,updateVoiceMappingRows,captureVoiceMappingDraft} from "../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js";

const config={voice_scope_policy:"device_mapping",voice_device_mappings:{office:"user:owner",living:"shared:household"}};
const panel={_agentId:"voice",_selectedAgent:()=>({entry_id:"provider"}),hass:{user:{id:"admin"}},
  _draft:structuredClone(config),_configData:{config:structuredClone(config)},
  _e:value=>String(value).replaceAll("&","&amp;").replaceAll('"',"&quot;").replaceAll("<","&lt;")};
const initial=renderVoiceIdentityCore(panel);
const state=voiceMappingDraft(panel),originalIds=state.rows.map(row=>row.id);
assert.equal(renderVoiceIdentityCore(panel),initial,"pristine lazy initialization does not change the rendered projection");
const invalid=structuredClone(state.rows);invalid[0].deviceId="living";
updateVoiceMappingRows(panel,invalid);
assert.deepEqual(panel._draft.voice_device_mappings,config.voice_device_mappings);
const before=structuredClone(state),projection=captureVoiceMappingDraft(panel);
const recovered=renderVoiceIdentityCore(panel);
assert.notEqual(recovered,initial,"the same canonical map cannot hide replacement authoring rows from DOM reconciliation");
assert.equal(renderVoiceIdentityCore(panel),recovered);
assert.deepEqual(state,before,"rendering reads the row projection without mutating state or generating IDs");
assert.deepEqual(captureVoiceMappingDraft(panel),projection);
assert.deepEqual(state.rows.map(row=>row.id),originalIds);
console.log("Voice recovery projection invalidation is stable and read-only");
