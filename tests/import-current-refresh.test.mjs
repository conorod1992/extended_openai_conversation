import assert from "node:assert/strict";
import test from "node:test";
import {fixture, retire} from "./management-ownership-harness.mjs";
import {applyConfigurationImport} from "../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js";

for (const supersede of [false, true]) test(`current import refreshes after dialog closure; newer draft=${supersede}`, async () => {
  const {state, create} = fixture(), panel = create();
  const dialog = {open:true, querySelectorAll:() => [], close() {this.open=false;}};
  const controls = new Map([
    ["#import-document", {value:"exported-json"}], ["#import-apply", {disabled:false}],
    ['input[name="import-mode"]:checked', {value:"current"}], ["#import-dialog", dialog],
  ]);
  panel.shadowRoot.querySelector = key => controls.get(key) || null;
  panel.shadowRoot.querySelectorAll = key => key === "dialog[open]" && dialog.open ? [dialog] : [];
  panel._importDocument="exported-json"; panel._importMode="current"; panel._importRevision=panel._configData.revision;
  const original=panel._hass.callWS;
  let refreshed=0;
  panel._call=async () => {
    state.agents[0].title="Imported title";
    state.records.get(panel._agentId).title="Imported title";
    return {status:"updated", subentry_id:panel._agentId};
  };
  panel._hass.callWS=async request => {
    if (request.action === "agents" && !request.section) {
      if (supersede) {panel._draft={chat_model:"later-model"}; panel._draftTitle="Later edit"; panel._draftAgentId=panel._agentId; panel._configDirty=true;}
      return {is_admin:true, agents:structuredClone(state.agents), scopes:[]};
    }
    return original(request);
  };
  panel._loadSection=async () => {refreshed++; panel._draftTitle=state.records.get(panel._agentId).title;};
  await applyConfigurationImport(panel, panel.shadowRoot);
  assert.equal(dialog.open,false);
  assert.equal(refreshed,supersede ? 0 : 1);
  assert.equal(panel._draftTitle,supersede ? "Later edit" : "Imported title");
  if (!supersede) assert.equal(panel._selectedAgent().title,"Imported title");
  retire(panel);
});
