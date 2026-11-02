import assert from 'node:assert/strict';
import {bindVoiceMappings} from '../custom_components/extended_openai_conversation_responses/frontend/voice-identity-ui.js';
import {voiceMappingDraft, voiceMappingDraftDirty, discardVoiceMappingDraft} from '../custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js';
import {deferred, TestEventTarget} from './frontend-test-helpers.mjs';

globalThis.customElements={get:()=>class {}};
function harness() {
  const users=deferred(),picker=new TestEventTarget(),satellite=new TestEventTarget(),type=new TestEventTarget(),card=new TestEventTarget();
  picker.isConnected=true;satellite.dataset={};type.value='user';
  const hidden={value:'user:owner'},device={value:'office'},warning={};
  const row={dataset:{},isConnected:true,querySelector(selector){return {'.voice-owner-user-picker':picker,'.voice-owner-type':type,'.voice-mapping-owner':hidden,'.voice-device-id':device,'.voice-satellite-picker':satellite,'.voice-owner-warning':warning,'.voice-satellite-warning':{}}[selector]||null;}};
  const config={voice_scope_policy:'device_mapping',voice_device_mappings:{office:'user:owner'}};
  const panel={_agentId:'agent',_draftAgentId:'agent',_selectedAgent:()=>({entry_id:'entry'}),_viewKey:()=> 'assistant/voice',_draft:structuredClone(config),_configData:{config:structuredClone(config)},_toast(){},hass:{user:{id:'admin'},callWS(message){if(message.type==='config/auth/list')return users.promise;if(message.type==='config/entity_registry/list')return Promise.resolve([{entity_id:'assist_satellite.office',device_id:'office'}]);throw Error(message.type);}},shadowRoot:{querySelector:selector=>selector==='#voice-mappings'?card:null,querySelectorAll:selector=>selector==='[data-voice-mapping-row]'?[row]:[]}};
  row.dataset.voiceRowId=voiceMappingDraft(panel).rows[0].id;
  bindVoiceMappings(panel);
  const select=async value=>{picker.value=value;await Promise.all([...picker.listeners.get('value-changed')||[]].map(listener=>listener({detail:{value}})));};
  return {users,picker,type,hidden,row,panel,select};
}
for(const outcome of ['success','failure']) {
  for(const edit of ['untouched','type-only','later-new','later-clear','later-shared','discard']) {
    const h=harness();assert.equal(h.picker.value,'');
    if(edit!=='untouched') {
      h.type.value='shared';await h.type.emit('change');h.type.value='user';await h.type.emit('change');
      assert.equal(h.hidden.value,'user:owner','temporary blank display cannot erase the intended saved user before hydration');
    }
    if(edit==='later-new')await h.select('other');
    if(edit==='later-clear')await h.select('');
    if(edit==='later-shared'){h.type.value='shared';await h.type.emit('change');}
    if(edit==='discard'){discardVoiceMappingDraft(h.panel);h.panel._draft.voice_device_mappings={office:'user:owner'};}
    const before=edit==='later-new'?'user:other':edit==='later-clear'?'unretained':edit==='later-shared'?'shared:household':'user:owner';
    assert.equal(h.panel._draft.voice_device_mappings.office,before,`${outcome}/${edit} is safe to Save before hydration`);
    if(outcome==='failure')h.users.reject(Error('User catalogue unavailable'));else h.users.resolve([{id:'owner',name:'Owner'},{id:'other',name:'Other'}]);
    await new Promise(setImmediate);
    const expected=edit==='later-new'?'user:other':edit==='later-clear'?'unretained':edit==='later-shared'?'shared:household':'user:owner';
    assert.equal(h.panel._draft.voice_device_mappings.office,expected,`${outcome}/${edit} keeps authored or restored mapping`);
    if(edit!=='discard')assert.equal(h.hidden.value,expected,`${outcome}/${edit} keeps visible owner and persistence value consistent`);
    if(['untouched','type-only','discard'].includes(edit))assert.equal(voiceMappingDraftDirty(h.panel),false,`${outcome}/${edit} does not invent a dirty draft`);
  }
}
console.log('Voice user hydration success/failure, later selection/clear, untouched rows and discard: 12 contracts passed');
