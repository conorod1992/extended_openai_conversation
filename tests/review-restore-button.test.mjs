import assert from 'node:assert/strict';
globalThis.window = {location:{pathname:'/extended-openai/usage-maintenance/backup-restore'}, addEventListener(){}, removeEventListener(){}};
globalThis.history = {pushState(){}};
globalThis.localStorage = {getItem(){return null;},setItem(){}};
globalThis.HTMLElement = class { attachShadow(){this.shadowRoot={querySelector(){return null;},hasChildNodes(){return false;}};} };
let defined;
globalThis.customElements = {define(_name, value){defined=value;},get(){return defined;},whenDefined(){return Promise.resolve();}};
const base = '../custom_components/extended_openai_conversation_responses/frontend/';
const {ExtendedOpenAIManagementPanel} = await import(base+'management-panel.js');
const {bindBackupTransfer} = await import(base+'backup-transfer-ui.js');
const {loadUsageWindow} = await import(base+'usage-data.js');
const {bindUsageDiagnostics} = await import(base+'usage-chart.js');
const panel = new ExtendedOpenAIManagementPanel();
panel._agentId='agent-a';
panel._page='usage-maintenance'; panel._subsection='backup-restore';
const agents=[{entry_id:'entry-a', subentry_id:'agent-a',title:'A'}];
panel._data={agents,scopes:[],is_admin:true};
panel._applyScopes=()=>{};
panel._loadSection=async()=>{};
panel._render=()=>{};
panel._confirm=async()=>true;
panel._setSaving=()=>{};
const messages=[],calls=[];
panel._toast=(...args)=>messages.push(args);
let total=100;
panel._hass={callWS:async message=>{
  calls.push(message.action);
  if(message.action==='daily') return {days:[{date:'2026-10-09',total_tokens:total}]};
  if(message.action==='import_restore'){total=200;return {restored:true};}
  if(message.action==='agents') return {agents,scopes:[],is_admin:true};
  throw Error('Unexpected '+message.action);
}};
const first=await loadUsageWindow(panel,'90','2026-10-09');
assert.equal(first.days[0].total_tokens,100);
const key='agent-a|usage-maintenance/usage|90';
panel._sectionCache.set(key,{days:first});
panel._eocSectionCacheTimes.set(key,Date.now());
panel._backupTransferSession='session';
panel._backupTransferPreviewToken='preview';
const handlers={};
const apply={disabled:false,dataset:{},addEventListener(type,fn){handlers[type]=fn;}};
const range={value:'90',disabled:false};
const root={
  querySelector(selector){return selector==='#restore-transfer-apply'?apply:selector==='#restore-dialog'?{close(){}}:selector==='#usage-window'?range:null;},
  querySelectorAll(selector){return selector==='.transfer-restore-section'?[{value:'usage',checked:true}]:[];},
};
panel.shadowRoot=root;
bindBackupTransfer(panel);
await handlers.click();
assert(messages.some(m=>m[0]==='Selected sections restored'));
panel._subsection='usage';panel._usageHistoryWindow='30';
panel._result={summary:{today:{date:'2026-10-09'}}};
bindUsageDiagnostics(panel);
await range.onchange({target:range});
assert.equal(panel._result.days.days[0].total_tokens,200);
assert.equal(total,200);
assert(!panel._sectionCache.has(key));
assert.deepEqual(calls,['daily','import_restore','agents','daily']);
console.log({calls,backendTotal:total,displayedTotal:panel._result.days.days[0].total_tokens,oldSectionCacheRetained:panel._sectionCache.has(key),messages});
