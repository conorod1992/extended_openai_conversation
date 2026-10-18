import assert from 'node:assert/strict';
import test from 'node:test';
import {bindConfiguration} from '../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js';
import {modelDataControls} from '../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js';
import {modelFieldPresentation} from '../custom_components/extended_openai_conversation_responses/frontend/agent-config-model-presentation.js';
import {renderManagement} from '../custom_components/extended_openai_conversation_responses/frontend/management-renderer.js';

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
  const efforts=['none','low','medium','high','xhigh','max'];
  const metadata=values=>({reasoning:{supported:true,efforts:values},recommended_profile:{reasoning_effort:'high'}});
  panel._modelCatalogData={requested_model:'gpt-5.6-sol',model_capabilities:metadata(efforts)};
  assert.deepEqual(modelFieldPresentation(panel,'reasoning_effort','low').efforts,efforts);
  let reads=0;
  panel._hass={async callWS(){reads++;return {model_capabilities:metadata([...efforts,'minimal']),catalog_version:2};}};
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

test('catalogue application preserves an explicitly edited effort',async()=>{
  const {panel,buttons,effort}=refreshingFixture('apply');
  effort.value='medium';
  panel._draft.reasoning_effort='medium';
  await buttons[1].click();
  assert.equal(panel._draft.reasoning_effort,'medium');
  assert.equal(panel._configData.config.reasoning_effort,'high');
});
