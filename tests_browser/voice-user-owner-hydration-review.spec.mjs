import {expect,test} from '@playwright/test';
import {fixtureUrl} from './browser-helpers.mjs';

test('delayed user catalogue keeps the displayed Voice owner and committed mapping consistent',async({page})=>{
  await page.addInitScript(()=>{if(!customElements.get('ha-user-picker'))customElements.define('ha-user-picker',class extends HTMLElement{});});
  for(const edit of ['type-only','later-new','later-clear','failure','untouched']) {
    await page.goto(fixtureUrl('assistant/basics'));
    await page.waitForFunction(()=>browserHarness?.panel?._configData?.revision);
    await page.evaluate(async()=>{
      const h=browserHarness,p=h.panel,s=h.getState().configuration;h.hass.user={id:'voice-admin'};
      await h.hass.callWS({type:'extended_openai_conversation_responses/management',section:'configuration',action:'save',entry_id:'entry-1',subentry_id:'agent-1',revision:s.revision,config:{...s.config,voice_scope_policy:'device_mapping',voice_device_mappings:{office:'user:owner',living:'shared:household'}}});
      const send=h.hass.callWS.bind(h.hass);
      h.hass.callWS=message=>{
        if(message.type==='config/auth/list')return new Promise((resolve,reject)=>{h.reviewReleaseVoiceUsers=resolve;h.reviewFailVoiceUsers=()=>reject(Error('User catalogue unavailable'));});
        if(message.type==='config/entity_registry/list')return Promise.resolve(['office','living'].map(id=>({entity_id:`assist_satellite.${id}`,device_id:id})));
        return send(message);
      };
      p._invalidateAfterMutation(p._agentId,'configuration','save');p._clearConfigDraft();await p._navigate('assistant','prompt-context');
    });
    const panel=page.locator('extended-openai-management-panel');
    await panel.locator('[data-config="prompt"]').fill(`Unrelated edit before ${edit} catalogue hydration`);
    await panel.evaluate(p=>p._navigate('assistant','voice'));
    const row=panel.locator('[data-voice-mapping-row]').first();
    await expect(panel.locator('[data-voice-mapping-row]')).toHaveCount(2);
    await expect(row.locator('.voice-owner-user-picker')).toHaveJSProperty('value','');
    if(edit!=='untouched') {
      await row.locator('.voice-owner-type').selectOption('shared');
      await row.locator('.voice-owner-type').selectOption('user');
    }
    if(['later-new','later-clear'].includes(edit))await row.locator('.voice-owner-user-picker').evaluate((picker,value)=>{picker.value=value;picker.dispatchEvent(new CustomEvent('value-changed',{detail:{value},bubbles:true,composed:true}));},edit==='later-new'?'other':'');
    const expected=edit==='later-new'?'user:other':edit==='later-clear'?'unretained':'user:owner';
    await panel.locator('#save-config').click();
    await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:expected,living:'shared:household'});
    await page.evaluate(edit=>edit==='failure'?browserHarness.reviewFailVoiceUsers():browserHarness.reviewReleaseVoiceUsers([{id:'owner',name:'Owner'},{id:'other',name:'Other'}]),edit);
    await expect(row.locator('.voice-owner-user-picker')).toHaveJSProperty('value',edit==='later-new'?'other':edit==='later-clear'?'':'owner');
    await expect(row.locator('.voice-mapping-owner')).toHaveValue(expected);
    await panel.evaluate(p=>p._navigate('assistant','prompt-context'));
    await panel.locator('[data-config="prompt"]').fill(`Unrelated edit after ${edit} catalogue hydration`);
    await panel.locator('#save-config').click();
    await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:expected,living:'shared:household'});
  }
});
