import {createInterface} from 'node:readline';
import {fixture,retire,saveConfiguration,retainPanelDrafts,restorePanelDrafts,stopDraftRecovery} from './management-ownership-harness.mjs';
import {applyConfigurationImport,refreshUncertainConfigurationImport} from '../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js';
const input=createInterface({input:process.stdin});
const replies=[];let waiting;
input.on('line',line=>{const data=JSON.parse(line);if(waiting){const resolve=waiting;waiting=null;resolve(data);}else replies.push(data);});
const read=()=>replies.length?Promise.resolve(replies.shift()):new Promise(resolve=>waiting=resolve);
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
const init=await read(),{create}=fixture();
const callWS=async request=>{
  if(request.type.endsWith('/model_catalog')) return {catalog_generation:init.baseline.catalog_generation,catalog_version:init.baseline.catalog_version,unchanged:true};
  send({request});const reply=await read();if(reply.error)throw reply.error;return reply.result;
};
function make(saved){
  const p=create();
  Object.assign(p,{_agentId:init.agent.subentry_id,_draftAgentId:init.agent.subentry_id,_scopeId:'user:admin',_data:{is_admin:true,agents:[init.agent]},_configData:saved,_result:saved,_draft:structuredClone(saved.config),_draftTitle:saved.title});
  p._hass={user:{id:'admin'},callWS};p._loadSection=async()=>p._loadConfigDraft({preserveResult:true});return p;
}
let panel=make(init.baseline);
const source=panel;
const document={value:init.document},apply={disabled:false},summary={textContent:''};
const dialog={open:true,querySelectorAll:()=>[],close(){this.open=false;}};
const controls=new Map([['#import-document',document],['#import-apply',apply],['#import-summary',summary],['input[name="import-mode"]:checked',{value:'current'}],['#import-dialog',dialog]]);
panel.shadowRoot.querySelector=key=>controls.get(key)||null;
panel.shadowRoot.querySelectorAll=key=>key==='dialog[open]'&&dialog.open?[dialog]:[];
panel._importDocument=document.value;panel._importMode='current';panel._importRevision=panel._configData.revision;
const snapshots=[];
const snapshot=stage=>snapshots.push({stage,revision:panel._configData?.revision,retention:panel._draft?.[init.field],previewRevision:panel._importRevision,barrier:Boolean(panel._eocImportRecovery),dirty:panel._configDirty,pendingRetention:Boolean(panel._eocPendingRetention),toast:panel.toasts.at(-1),summary:summary.textContent});
await applyConfigurationImport(panel,panel.shadowRoot);snapshot('committed-import-failure');
if(init.replacement){
  source.isConnected=false;retainPanelDrafts(source);stopDraftRecovery(source);
  const saved=await source._call('configuration','get');
  panel=make(saved);restorePanelDrafts(panel);stopDraftRecovery(panel);snapshot('replacement');
  panel._draftTitle='Unrelated title edit';panel._syncConfigDirty();
  await saveConfiguration(panel,null);snapshot('unrelated-title-save');
  send({done:snapshots});retire(source,panel);input.close();
}else{
if(init.force_refresh){panel._configDataStale=true;await panel._loadConfigDraft({preserveResult:true});}
// The actual Preview listener captures this revision before refreshing the catalogue.
let revision=panel._configData.revision;
if(await refreshUncertainConfigurationImport(panel,{configuration:true})) revision=panel._configData.revision;
const preview=await panel._call('configuration','import_preview',{document:document.value,mode:'current'});
if(!preview.valid)throw Error('Preview rejected');
panel._importDocument=document.value;panel._importMode='current';panel._importRevision=revision;
snapshot('preview-after-failure');
await applyConfigurationImport(panel,panel.shadowRoot);snapshot('retry-import');
send({done:snapshots});retire(panel);input.close();
}
