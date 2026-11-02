import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test("Held Voice user catalogues respect discarded and recovered row ownership",async({page})=>{
  await page.addInitScript(()=>{if(!customElements.get("ha-user-picker"))customElements.define("ha-user-picker",class extends HTMLElement{});});
  for(const bundled of [false,true]) for(const action of ["discard","recover-clear"]) for(const outcome of ["success","failure"]) {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",bundled?"&bundle=1":""));
    await page.waitForFunction(()=>browserHarness?.panel?._configData?.revision);
    await page.evaluate(async()=>{
      const h=browserHarness,p=h.panel,s=h.getState().configuration;h.hass.user={id:"voice-hourly2-admin"};
      await h.hass.callWS({type:"extended_openai_conversation_responses/management",section:"configuration",action:"save",entry_id:"entry-1",subentry_id:"agent-1",revision:s.revision,config:{...s.config,voice_scope_policy:"device_mapping",voice_device_mappings:{office:"user:owner",living:"shared:household"}}});
      const send=h.hass.callWS.bind(h.hass);
      h.hass.callWS=message=>{
        if(message.type==="config/auth/list") {
          if(h.heldUsersFailure) return Promise.reject(Error("Held catalogue failed"));
          if(h.heldUsers) return Promise.resolve(h.heldUsers);
          return new Promise((resolve,reject)=>{
            h.resolveHeldUsers=users=>{h.heldUsers=users;resolve(users);};
            h.rejectHeldUsers=()=>{h.heldUsersFailure=true;reject(Error("Held catalogue failed"));};
          });
        }
        if(message.type==="config/entity_registry/list")return Promise.resolve(["office","living"].map(id=>({entity_id:`assist_satellite.${id}`,device_id:id})));
        return send(message);
      };
      p._invalidateAfterMutation(p._agentId,"configuration","save");p._clearConfigDraft();await p._navigate("assistant","voice");
    });
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
    await panel.locator(".voice-owner-user-picker").first().evaluate(picker=>{
      picker.value="other";picker.dispatchEvent(new CustomEvent("value-changed",{detail:{value:"other"},bubbles:true,composed:true}));
    });
    if(action==="discard") await panel.locator("#revert-config").click();
    else {
      await page.evaluate(()=>{
        const h=browserHarness,old=h.panel;old.remove();
        const target=document.createElement("extended-openai-management-panel");target.hass=h.hass;document.body.append(target);h.panel=target;
      });
      await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
      await expect.poll(()=>panel.evaluate(p=>p._draft.voice_device_mappings.office)).toBe("user:other");
      await panel.locator(".voice-owner-user-picker").first().evaluate(picker=>{
        picker.value="";picker.dispatchEvent(new CustomEvent("value-changed",{detail:{value:""},bubbles:true,composed:true}));
      });
    }
    await page.evaluate(outcome=>outcome==="failure"?browserHarness.rejectHeldUsers():browserHarness.resolveHeldUsers([{id:"owner",name:"Owner"},{id:"other",name:"Other"}]),outcome);
    const expected=action==="discard"?"user:owner":"unretained";
    await expect(panel.locator(".voice-mapping-owner").first()).toHaveValue(expected);
    await expect(panel.locator(".voice-owner-user-picker").first()).toHaveJSProperty("value",action==="discard"?"owner":"");
    expect(await panel.evaluate(p=>p._draft.voice_device_mappings)).toEqual({office:expected,living:"shared:household"});
    if(action==="discard") {
      expect(await panel.evaluate(p=>p._configDirty)).toBe(false);
      await panel.evaluate(p=>p._navigate("assistant","prompt-context"));
      await panel.locator('[data-config="prompt"]').fill(`Unrelated saved prompt after ${outcome} discarded catalogue`);
    }
    await panel.locator("#save-config").click();
    await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:expected,living:"shared:household"});
    await expectHarnessClean(page,errors);
  }
});
