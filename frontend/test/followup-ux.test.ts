// @ts-nocheck
import {describe, expect, it} from "vitest";
import {localRuleActionSummary} from "../../custom_components/extended_openai_conversation_responses/frontend/request-rule-response-summary.js";
import {requestRuleSummary} from "../../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js";
import {requestRuleSummary as guidanceSummary} from "../../custom_components/extended_openai_conversation_responses/frontend/management-decision-guidance.js";
import {initializeVoiceUserPicker, voiceUserCatalogue} from "../../custom_components/extended_openai_conversation_responses/frontend/voice-user-picker.js";

describe("local rule final response guidance", () => {
  const base = {success_response:"Done", failure_response:"Failed"};
  it.each([
    [[{set_conversation_response:"First"},{set_conversation_response:"Final"}],false,'replies “Final”'],
    [[{set_conversation_response:"Final"}],true,'replies “Final”'],
    [[{stop:"Finished"},{set_conversation_response:"Unreachable"}],true,'replies “Done”'],
    [[{set_conversation_response:"Final"},{stop:"Finished"}],true,'replies “Final”'],
    [[{set_conversation_response:"Final"},{stop:"Error",error:true}],true,'replies “Failed”'],
    [[{set_conversation_response:"Final"},{set_conversation_response:null}],true,'then continues to AI'],
    [[{set_conversation_response:"Ignored",enabled:false}],false,'replies “Done”'],
    [[{choose:[]}],true,'response depends on local actions'],
    [[{set_conversation_response:"{{ result }}"}],true,'conversation response template'],
  ])("describes actual response precedence", (actions,continue_to_ai,expected) => {
    const rule={action_type:"local_action",action:{...base,actions,continue_to_ai}};
    expect(localRuleActionSummary(rule.action)).toContain(expected);
    expect(requestRuleSummary(rule).action).toBe(guidanceSummary(rule).action);
  });
});

describe("authoritative Voice user catalogue", () => {
  it("includes secondary users absent from scopes and rejects a previous connection's catalogue", () => {
    const connection={};const panel={hass:{connection},_voiceUsersConnection:connection,_voiceUsers:[{id:"secondary",name:"Secondary"},{id:"system",name:"System",system_generated:true}],_baseScopes:[]};
    expect(voiceUserCatalogue(panel)).toEqual([{id:"secondary",name:"Secondary"}]);
    panel.hass={connection:{}};
    expect(voiceUserCatalogue(panel)).toEqual([]);
  });
});


describe("cold picker hydration ownership", () => {
  function fixture() {
    let resolve;
    const request=new Promise(done=>{resolve=done;});
    const writes=[];
    const picker=new EventTarget();
    Object.assign(picker,{isConnected:true,ownerDocument:{defaultView:{customElements:{get:()=>true}}}});
    let value;
    Object.defineProperty(picker,"value",{get:()=>value,set:next=>{writes.push([next,picker.users]);value=next;}});
    const panel={hass:{connection:{},callWS:()=>request},_agentId:"agent",_viewKey:()=>"assistant/voice"};
    return {picker,panel,writes,resolve};
  }
  it("assigns authoritative users before a saved value can render as unknown", async () => {
    const {picker,panel,writes,resolve}=fixture();
    const ready=initializeVoiceUserPicker(panel,picker,"user:secondary");
    expect(picker.value).toBe("");
    const users=[{id:"secondary",name:"Secondary"}];resolve(users);await ready;
    expect(writes.at(-1)).toEqual(["secondary",users]);
  });
  it("preserves a selection cleared by the user while loading", async () => {
    const {picker,panel,resolve}=fixture();
    const ready=initializeVoiceUserPicker(panel,picker,"secondary");
    picker.value="";picker.dispatchEvent(new Event("value-changed"));
    resolve([{id:"secondary",name:"Secondary"}]);await ready;
    expect(picker.value).toBe("");
  });
  it("rejects a catalogue arriving for a different authenticated connection", async () => {
    const {picker,panel,resolve}=fixture();
    const ready=initializeVoiceUserPicker(panel,picker,"secondary");
    panel.hass={connection:{}};resolve([{id:"secondary",name:"Secondary"}]);await ready;
    expect(panel._voiceUsers).toBeUndefined();expect(picker.users).toBeUndefined();
  });
});
