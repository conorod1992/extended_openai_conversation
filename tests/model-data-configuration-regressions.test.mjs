import assert from 'node:assert/strict';
import test from 'node:test';
import {bindConfiguration} from '../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js';
import {modelDataControls, lookupModelData, observeCatalogGeneration} from '../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js';
import {modelFieldPresentation} from '../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js';
import {renderManagement} from '../custom_components/extended_openai_conversation_responses/frontend/management-renderer.js';
import {writeSectionCache} from '../custom_components/extended_openai_conversation_responses/frontend/management-cache.js';

function fixture(action, oldEffort='low', newEffort='low') {
  const buttons=['check','apply','reset'].map(action=>({dataset:{modelData:action},disabled:action==='apply',hidden:action==='apply',addEventListener(_,fn){this.click=fn;}}));
  const status={textContent:''};
  const main={dataset:{},toggleAttribute(){},setAttribute(){},classList:{add(){},remove(){}},set innerHTML(value){this.remounted=true;}};
  const effort={value:oldEffort,dataset:{config:'reasoning_effort'},matches:()=>true};
  const root={
    querySelectorAll(selector){if(selector==='[data-model-data]')return buttons;if(selector.startsWith('[data-config],'))return [effort];return [];},
    querySelector(selector){if(selector==='[data-eoc-persistent-shell]')return {};if(selector==='[data-eoc-main]')return main;if(selector==='[data-model-data-status]')return status;if(selector==='[data-model-data="apply"]')return buttons[1];return null;},
    addEventListener() {},
  };
  const metadata=effort=>({reasoning:{supported:true,efforts:['low','high']},recommended_profile:{reasoning_effort:effort}});
  const panel={shadowRoot:root,_agentId:'agent',_viewKey:()=> 'assistant/model-responses',_loadToken:1,_cacheGeneration:1,
    _draft:{chat_model:'gpt-6-astra',reasoning_effort:oldEffort,api_mode:'responses'},_result:{},_toast(){},
    _modelCatalogData:{catalog_version:1,update_available:false,requested_model:'gpt-6-astra',model_capabilities:metadata(oldEffort)},
    _hass:{async callWS(){return {catalog_version:action==='apply'?2:1,update_available:false,model_capabilities:metadata(newEffort)};}},
  };
  panel._data={}; panel._canAccessView=()=>false; panel._selectedAgent=()=>({subentry_id:'agent'});
  panel._visibleSubsections=()=>[];panel._dialogs=()=>'';panel._content=()=>modelDataControls(panel);
  panel._eocMainMarkup=panel._content();panel._eocRenderedRoute='agent|assistant/model-responses';panel._eocDialogMarkup='';
  panel._render=()=>{renderManagement(panel);panel.remountNeeded=Boolean(main.remounted);};
  bindConfiguration(panel);
  return {panel,buttons,effort};
}

globalThis.window = {location:{pathname:'/extended-openai/assistant/basics'}, addEventListener(){}, removeEventListener(){}};
globalThis.history = {pushState(){}};
globalThis.localStorage = {getItem(){return null;}, setItem(){}};
globalThis.HTMLElement = class {attachShadow(){this.shadowRoot={querySelector(){return null;},querySelectorAll(){return [];}};}};
globalThis.customElements = {define(){},get(){},whenDefined(){return Promise.resolve();}};
const {ExtendedOpenAIManagementPanel} = await import('../custom_components/extended_openai_conversation_responses/frontend/management-panel.js');
const {bindConfiguration: bindModelConfiguration} = await import('../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js');

test('a successful no-update check releases the retained configuration buttons', async () => {
  const {panel,buttons} = fixture('check');
  const draft = panel._draft;
  await buttons[0].click();
  assert.notEqual(panel._draft,draft,'the real callback reads a replacement draft');
  assert.equal(panel.remountNeeded,false,'identical markup retains the disabled DOM buttons');
  assert.deepEqual(buttons.map(button=>button.disabled),[false,true,false]);
});

for (const detached of [false,true]) test(`an unrelated invalidation releases still-owned model buttons, detached=${detached}`, async () => {
  const {panel,buttons}=fixture('check');
  const draft=panel._draft;
  let release;
  panel._hass.callWS=()=>new Promise(resolve=>{release=resolve;});
  const checking=buttons[0].click();
  assert.deepEqual(buttons.map(button=>button.disabled),[true,true,true]);
  panel._cacheGeneration++;
  panel.isConnected=!detached;
  release({catalog_version:1,update_available:true,model_capabilities:{stale:true}});
  await checking;
  assert.equal(panel._draft,draft,'stale data must not reach the draft-reading callback');
  assert.equal(panel._modelCatalogData.update_available,false);
  assert.deepEqual(buttons.map(button=>button.disabled),[false,true,false]);
});

test('an abandoned action cannot release replacement model buttons', async () => {
  const {panel,buttons}=fixture('check');
  let release;
  panel._hass.callWS=()=>new Promise(resolve=>{release=resolve;});
  const checking=buttons[0].click();
  const replacements=buttons.map(button=>({...button,disabled:true}));
  const original=panel.shadowRoot.querySelectorAll.bind(panel.shadowRoot);
  panel.shadowRoot.querySelectorAll=selector=>selector==='[data-model-data]' ? replacements : original(selector);
  release({catalog_version:1,update_available:false});
  await checking;
  assert.deepEqual(buttons.map(button=>button.disabled),[true,true,true]);
  assert.deepEqual(replacements.map(button=>button.disabled),[true,true,true]);
});

function refreshingFixture(action) {
  const fixtureData = fixture(action,'low','high');
  const panel = Object.assign(new ExtendedOpenAIManagementPanel(),fixtureData.panel);
  fixtureData.panel = panel;
  panel._data={agents:[{entry_id:'entry',subentry_id:'agent'}],is_admin:true};
  panel._selectedAgent=()=>panel._data.agents[0];
  panel._draftAgentId='agent';
  panel._configData={revision:'r1',title:'Saved',config:{...panel._draft,prompt:'Saved'}};
  panel._draft={...panel._configData.config,prompt:'Unsaved'};
  panel._draftTitle='Unsaved title';
  panel._configDirty=true;
  panel._result=panel._configData;
  panel._call=async()=>({...panel._configData,config:{...panel._configData.config,reasoning_effort:'high'}});
  panel._rememberCleanConfiguration(panel._configData);
  panel._cleanConfigSnapshots.set('other-agent',{result:panel._configData});
  panel._render=()=>{};
  bindConfiguration(panel);
  return fixtureData;
}

for (const action of ['apply','reset']) {
  test(`${action} refreshes inherited effort while retaining local edits`,async()=>{
    const {panel,buttons}=refreshingFixture(action);
    await buttons[action==='apply'?1:2].click();
    assert.equal(panel._draft.reasoning_effort,'high');
    assert.equal(panel._draft.prompt,'Unsaved');
    assert.equal(panel._draftTitle,'Unsaved title');
    assert.equal(panel._cleanConfigSnapshots.has('other-agent'),false);
    assert.equal(modelFieldPresentation(panel,'reasoning_effort','high').value,'high');
    assert.deepEqual(buttons.map(button=>button.disabled),[false,true,false]);
  });
}

for (const navigation of ['native history','panel']) {
test(`${navigation} route navigation refreshes a warm tab after another tab applies a catalogue`,async()=>{
  const {panel}=refreshingFixture('check');
  const query=panel.shadowRoot.querySelector.bind(panel.shadowRoot);
  panel.shadowRoot.querySelector=selector=>selector==='[data-config="reasoning_effort"]' ? {value:'low'} : query(selector);
  delete panel._viewKey;
  panel._page='assistant'; panel._subsection='basics';
  panel._draft.chat_model='gpt-5.6-sol';
  panel._configData.config.chat_model='gpt-5.6-sol';
  panel._configData.catalog_generation=1;
  panel._call=async()=>({...panel._configData,catalog_generation:2,config:{...panel._configData.config,reasoning_effort:'high'}});
  const efforts=['none','low','medium','high','xhigh','max'];
  const metadata=values=>({reasoning:{supported:true,efforts:values},recommended_profile:{reasoning_effort:'high'}});
  panel._modelCatalogData={requested_model:'gpt-5.6-sol',model_capabilities:metadata(efforts)};
  assert.deepEqual(modelFieldPresentation(panel,'reasoning_effort','low').efforts,efforts);
  let reads=0;
  panel._hass={async callWS(message){
    if(message.action==='status') return {catalog_generation:2,catalog_version:2};
    reads++;return {model_capabilities:metadata([...efforts,'minimal']),catalog_version:2,catalog_generation:2};
  }};
  let loading;
  panel._loadSection=()=>loading=(async()=>{await panel._loadConfigDraft();bindModelConfiguration(panel);})();
  window.location.pathname='/extended-openai/assistant/model-responses';
  if (navigation==='native history') await panel._syncRouteFromLocation();
  else await panel._navigate('assistant','model-responses');
  assert.ok(!panel._error,panel._error);
  await loading;
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(reads,1);
  assert.deepEqual(modelFieldPresentation(panel,'reasoning_effort','high').efforts,[...efforts,'minimal']);
  assert.equal(panel._draft.prompt,'Unsaved');
  assert.equal(panel._draft.reasoning_effort,'high');
});
}

for (const view of ['assistant/prompt-context','assistant/voice','capabilities/home-assistant','data-memory/memory-settings','usage-maintenance/retention']) {
  test(`the shared configuration boundary refreshes catalogue defaults on ${view}`,async()=>{
    const {panel}=refreshingFixture('check');
    panel._viewKey=()=>view;
    panel._configData.catalog_generation=10;
    panel._eocCatalogGeneration=10;
    panel._rememberCleanConfiguration(panel._configData);
    let statusReads=0, configReads=0;
    panel._hass={async callWS(message){assert.equal(message.action,'status');statusReads++;return {catalog_generation:11,catalog_version:2};}};
    panel._call=async()=>{configReads++;return {...panel._configData,catalog_generation:11,config:{...panel._configData.config,reasoning_effort:'high'}};};
    await panel._loadConfigDraft();
    assert.equal(statusReads,1);
    assert.equal(configReads,1);
    assert.equal(panel._configData.catalog_generation,11);
    assert.equal(panel._draft.reasoning_effort,'high');
    assert.equal(panel._draft.prompt,'Unsaved');
    assert.equal(panel._draftTitle,'Unsaved title');
    assert.equal(panel._cleanConfigSnapshots.has('other-agent'),false);
    await panel._loadConfigDraft();
    assert.equal(statusReads,1,'the same section load reuses its generation validation');
    assert.equal(configReads,1);
  });
}

test('the section-loader fast path validates catalogue generation before using active configuration',async()=>{
  const {panel}=refreshingFixture('check');
  panel._viewKey=()=> 'assistant/prompt-context';
  panel._configData.catalog_generation=10;
  panel._eocCatalogGeneration=10;
  writeSectionCache(panel,panel._sectionCacheKey(),panel._configData);
  panel._hass={async callWS(message){assert.equal(message.action,'status');return {catalog_generation:11};}};
  panel._call=async()=>({...panel._configData,catalog_generation:11,config:{...panel._configData.config,reasoning_effort:'high'}});
  await panel._loadSectionData();
  assert.ok(!panel._error,panel._error);
  assert.equal(panel._draft.reasoning_effort,'high');
  assert.equal(panel._draft.prompt,'Unsaved');
});

test('a new generation refreshes a cached agent even without an active baseline',async()=>{
  const {panel}=refreshingFixture('check');
  panel._rememberCleanConfiguration({...panel._configData,catalog_generation:1});
  panel._configData=null;
  panel._draft=null;
  panel._draftAgentId=null;
  panel._configDirty=false;
  panel._hass={async callWS(){return {catalog_generation:2};}};
  let reads=0;
  panel._call=async()=>{reads++;return {catalog_generation:2,revision:'r1',title:'Saved',config:{reasoning_effort:'high'}};};
  await panel._loadConfigDraft();
  assert.equal(reads,1);
  assert.equal(panel._draft.reasoning_effort,'high');
});

for (const action of ['apply','reset']) {
  test(`a successful ${action} from a stale view still invalidates every agent's catalogue snapshots`,async()=>{
    const {panel}=refreshingFixture('check');
    const previousPresentation=panel._modelCatalogData;
    panel._hass={async callWS(){return {catalog_generation:3,catalog_version:3,model_capabilities:{}};}};
    await lookupModelData(panel,'old-model',action,()=>false);
    assert.equal(panel._eocCatalogGeneration,3);
    assert.equal(panel._cleanConfigSnapshots.size,0);
    assert.equal(panel._configDataStale,true);
    assert.equal(panel._modelCatalogData,null);
    assert.notEqual(panel._modelCatalogData,previousPresentation);
    assert.equal(panel._draft.prompt,'Unsaved');
  });
}

test('configuration snapshots from another catalogue generation cannot hydrate a cached agent',()=>{
  const {panel}=refreshingFixture('check');
  panel._data.agents.push({entry_id:'entry',subentry_id:'other'});
  panel._rememberCleanConfiguration({...panel._configData,catalog_generation:1},'other');
  observeCatalogGeneration(panel,{catalog_generation:2});
  panel._rememberCleanConfiguration({...panel._configData,catalog_generation:1},'other');
  assert.equal(panel._freshCleanConfiguration('other','full'),null,'a late cache insertion also fails the generation check');
});

test('a failed catalogue status check preserves the draft and can be retried',async()=>{
  const {panel}=refreshingFixture('check');
  panel._configData.catalog_generation=1;
  panel._hass={async callWS(){throw new Error('status unavailable');}};
  await assert.rejects(panel._loadConfigDraft(),/status unavailable/);
  assert.equal(panel._draft.prompt,'Unsaved');
  panel._hass={async callWS(){return {catalog_generation:2};}};
  panel._call=async()=>({...panel._configData,catalog_generation:2,config:{...panel._configData.config,reasoning_effort:'high'}});
  await panel._loadConfigDraft();
  assert.equal(panel._draft.reasoning_effort,'high');
  assert.equal(panel._draft.prompt,'Unsaved');
});

test('a changed catalogue version also invalidates snapshots when a restarted server reuses a generation number',async()=>{
  const {panel}=refreshingFixture('check');
  Object.assign(panel._configData,{catalog_generation:1,catalog_version:1});
  panel._eocCatalogGeneration=1;
  panel._eocCatalogVersion=1;
  panel._rememberCleanConfiguration(panel._configData);
  panel._hass={async callWS(){return {catalog_generation:1,catalog_version:2};}};
  panel._call=async()=>({...panel._configData,catalog_generation:1,catalog_version:2,config:{...panel._configData.config,reasoning_effort:'high'}});
  await panel._loadConfigDraft();
  assert.equal(panel._draft.reasoning_effort,'high');
  assert.equal(panel._draft.prompt,'Unsaved');
  panel._rememberCleanConfiguration({...panel._configData,catalog_version:1});
  assert.equal(panel._freshCleanConfiguration('agent','full'),null);
});

test('an older baseline installed after generation validation is refreshed before reuse',async()=>{
  const {panel}=refreshingFixture('check');
  Object.assign(panel._configData,{catalog_generation:1,catalog_version:1});
  panel._eocCatalogGeneration=2;
  panel._eocCatalogVersion=2;
  panel._eocCatalogValidation={agentId:panel._agentId,entryId:'entry',view:panel._viewKey(),loadToken:panel._loadToken,cacheGeneration:panel._cacheGeneration,generation:2,version:2};
  panel._hass={async callWS(){return {catalog_generation:2,catalog_version:2};}};
  panel._call=async()=>({...panel._configData,catalog_generation:2,catalog_version:2,config:{...panel._configData.config,reasoning_effort:'high'}});
  await panel._loadConfigDraft();
  assert.equal(panel._draft.reasoning_effort,'high');
  assert.equal(panel._draft.prompt,'Unsaved');
});

for (const obsolete of [{catalog_generation:1,catalog_version:2},{catalog_generation:2,catalog_version:1}]) {
  test(`a prefetched configuration from obsolete catalogue ${JSON.stringify(obsolete)} is discarded`,async()=>{
    const {panel}=refreshingFixture('check');
    Object.assign(panel._configData,{catalog_generation:2,catalog_version:2});
    panel._eocCatalogGeneration=2;
    panel._eocCatalogVersion=2;
    panel._configDataStale=true;
    panel._draft={...panel._configData.config};
    panel._draftTitle=panel._configData.title;
    panel._configDirty=false;
    const detail={status:'started'};
    panel._eocConfigurationReadDiagnostics={prefetch:detail};
    panel._eocStoredConfigPrefetch={view:panel._viewKey(),entryId:'entry',subentryId:'agent',action:'get',cacheGeneration:panel._cacheGeneration,detail,
      promise:Promise.resolve({status:'fulfilled',value:{...panel._configData,...obsolete}})};
    panel._hass={async callWS(){return {catalog_generation:2,catalog_version:2};}};
    let reads=0;
    panel._call=async()=>{reads++;return {...panel._configData,config:{...panel._configData.config,reasoning_effort:'high'}};};
    await panel._loadConfigDraft();
    assert.equal(reads,1);
    assert.equal(panel._draft.reasoning_effort,'high');
    assert.equal(detail.reason,'catalogue-changed');
  });
}

test('catalogue application preserves an explicitly edited effort',async()=>{
  const {panel,buttons,effort}=refreshingFixture('apply');
  effort.value='medium';
  panel._draft.reasoning_effort='medium';
  await buttons[1].click();
  assert.equal(panel._draft.reasoning_effort,'medium');
  assert.equal(panel._configData.config.reasoning_effort,'high');
});

for (const action of ['apply','reset']) {
  test(`${action} reports a follow-up configuration failure and permits retry without losing edits`,async()=>{
    const {panel,buttons}=refreshingFixture(action);
    const refreshed=panel._call;
    panel._call=async()=>{throw new Error('configuration/get unavailable');};
    const notices=[];
    panel._toast=(message,error)=>notices.push({message,error});
    await buttons[action==='apply'?1:2].click();
    const status=panel.shadowRoot.querySelector('[data-model-data-status]').textContent;
    assert.match(status,/Model data updated, but assistant settings could not be refreshed/);
    assert.match(status,/configuration\/get unavailable/);
    assert.match(status,/return to retry/);
    assert.equal(notices.at(-1).error,true);
    assert.equal(panel._configDataStale,true);
    assert.equal(panel._draft.prompt,'Unsaved');
    assert.deepEqual(buttons.map(button=>button.disabled),[false,true,false]);
    panel._call=refreshed;
    await panel._loadConfigDraft();
    assert.equal(panel._draft.reasoning_effort,'high');
    assert.equal(panel._draft.prompt,'Unsaved');
    assert.equal(panel._draftTitle,'Unsaved title');
    assert.equal(panel._configDataStale,false);
  });
}

test('capability gates follow the draft catalogue after saved baseline replacement', async () => {
  const {modelCapabilitiesForDraft} = await import('../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js');
  const current = {supports_reasoning_effort:true, reasoning:{supported:true, efforts:['minimal','low']}};
  const saved = {supports_reasoning_effort:false};
  const panel = {_draft:{chat_model:'gpt-5-mini',reasoning_effort:'minimal'},_result:{config:{chat_model:'gpt-4o'},model_capabilities:saved},_modelCatalogData:{requested_model:'gpt-5-mini',model_capabilities:current}};
  assert.equal(modelCapabilitiesForDraft(panel), current);
  assert.equal(modelFieldPresentation(panel,'reasoning_effort','minimal').visible,true);
  panel._modelCatalogData.requested_model = 'gpt-4o';
  assert.deepEqual(modelCapabilitiesForDraft(panel), {}, 'a mismatched catalogue and saved baseline cannot describe this draft');
  panel._draft = {chat_model:'azure-deployment',azure_model:'gpt-5-mini'};
  panel._modelCatalogData = {requested_model:'gpt-5-mini',model_metadata:current};
  assert.equal(modelCapabilitiesForDraft(panel), current);
  delete panel._modelCatalogData;
  panel._draft = {chat_model:'gpt-4o'};
  assert.equal(modelCapabilitiesForDraft(panel), saved);
  delete panel._draft;
  assert.equal(modelCapabilitiesForDraft(panel), saved);
});
