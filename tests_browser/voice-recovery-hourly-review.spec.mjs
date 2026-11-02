import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test("Voice recovery publishes invalid rows into an already edited replacement panel",async({page})=>{
  for(const finish of ["correct","discard"]) {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  await page.waitForFunction(()=>browserHarness?.panel?._configData?.revision);
  await page.evaluate(async()=>{
    const h=browserHarness,p=h.panel,s=h.getState().configuration;
    h.hass.user={id:"voice-review-admin"};
    await h.hass.callWS({type:"extended_openai_conversation_responses/management",section:"configuration",action:"save",entry_id:"entry-1",subentry_id:"agent-1",revision:s.revision,title:s.title,config:{...s.config,voice_scope_policy:"device_mapping",voice_device_mappings:{office:"user:owner",living:"shared:household"}}});
    const send=h.hass.callWS.bind(h.hass);
    h.hass.callWS=async request=>{
      if(request.type==="config/entity_registry/list") return ["office","living"].map(id=>({entity_id:`assist_satellite.${id}`,device_id:id}));
      if(request.type==="config/entity_registry/get") return {entity_id:request.entity_id,device_id:request.entity_id.split(".")[1]};
      const result=await send(request);
      if(h.holdVoiceSave && request.section==="configuration" && request.action==="save") {
        h.holdVoiceSave=false;h.savedVoiceResponse=result;
        await new Promise(resolve=>{h.releaseVoiceSave=resolve;});
      }
      return result;
    };
    p._invalidateAfterMutation(p._agentId,"configuration","save");p._clearConfigDraft();await p._navigate("assistant","prompt-context");
  });
  let panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="prompt"]').fill("Submitted before HA replaces the panel");
  await panel.evaluate(p=>p._navigate("assistant","voice"));
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await page.evaluate(()=>{browserHarness.holdVoiceSave=true;});
  await panel.locator("#save-config").click();
  await page.waitForFunction(()=>browserHarness.releaseVoiceSave);
  await panel.locator(".voice-satellite-picker").nth(0).evaluate(picker=>{
    picker.value="assist_satellite.living";
    picker.dispatchEvent(new CustomEvent("value-changed",{detail:{value:picker.value},bubbles:true,composed:true}));
  });
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
  await page.evaluate(()=>{
    const h=browserHarness,old=h.panel;h.reviewSource=old;old.remove();
    const target=document.createElement("extended-openai-management-panel");
    target.hass=h.hass;document.body.append(target);h.panel=target;
  });
  panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await panel.evaluate(p=>p._navigate("assistant","prompt-context"));
  await panel.locator('[data-config="prompt"]').fill("Newer prompt owned by replacement");
  await panel.evaluate(p=>p._navigate("assistant","voice"));
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await expect(panel.locator("#save-config")).toBeVisible();
  await page.evaluate(()=>browserHarness.releaseVoiceSave());
  await expect.poll(()=>panel.evaluate(async p=>{
    const {captureVoiceMappingDraft}=await import("/custom_components/extended_openai_conversation_responses/frontend/voice-mapping-draft.js");
    return captureVoiceMappingDraft(p)?.map(row=>row.deviceId);
  })).toEqual(["living","living"]);
  await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("living");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
  if(finish==="correct") {
    await panel.locator(".voice-satellite-picker").nth(0).evaluate(picker=>{
      picker.value="assist_satellite.office";
      picker.dispatchEvent(new CustomEvent("value-changed",{detail:{value:picker.value},bubbles:true,composed:true}));
    });
    await expect(panel.locator('[data-error="voice_device_mappings"]')).toBeEmpty();
    await panel.locator("#save-config").click();
    await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.prompt)).toBe("Newer prompt owned by replacement");
  } else {
    await panel.locator("#revert-config").click();
    await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("office");
    expect(await panel.evaluate(p=>p._draft.prompt)).toBe("Submitted before HA replaces the panel");
    expect(await panel.evaluate(p=>p._configDirty)).toBe(false);
  }
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:"user:owner",living:"shared:household"});
  await expectHarnessClean(page,errors);
  }
});
