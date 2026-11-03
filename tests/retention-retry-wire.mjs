import {createInterface} from 'node:readline';
import {fixture, edit, retire, saveConfiguration} from './management-ownership-harness.mjs';
const input=createInterface({input:process.stdin});
const replies=[]; let waiting;
input.on('line',line=>{const data=JSON.parse(line); if(waiting){const resolve=waiting;waiting=null;resolve(data);}else replies.push(data);});
const read=()=>replies.length?Promise.resolve(replies.shift()):new Promise(resolve=>waiting=resolve);
const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
const init=await read();
const {create}=fixture(),panel=create();
Object.assign(panel,{_agentId:init.agent.subentry_id,_draftAgentId:init.agent.subentry_id,_data:{is_admin:true,agents:[init.agent]},_configData:init.baseline,_result:init.baseline,_draft:structuredClone(init.baseline.config),_draftTitle:init.baseline.title});
panel._hass={user:{id:'admin'},callWS:async request=>{if(request.type.endsWith('/model_catalog'))return {catalog_generation:init.baseline.catalog_generation,catalog_version:init.baseline.catalog_version,unchanged:true};send({request});const reply=await read();if(reply.error)throw reply.error;return reply.result;}};
const snapshots=[];
function snapshot(label){snapshots.push({label,revision:panel._configData.revision,dirty:panel._configDirty,stale:panel._configDataStale,retention:panel._draft.usage_request_retention_days,toast:panel.toasts.at(-1)});}
edit(panel,'usage_request_retention_days',0);
await saveConfiguration(panel,null);snapshot('first-save');
await saveConfiguration(panel,null);snapshot('retry-save');
await panel._loadConfigDraft();snapshot('normal-refresh');panel._draftTitle='Unrelated title edit';panel._syncConfigDirty();await saveConfiguration(panel,null);snapshot('unrelated-title-save');
send({done:snapshots});
retire(panel);input.close();
