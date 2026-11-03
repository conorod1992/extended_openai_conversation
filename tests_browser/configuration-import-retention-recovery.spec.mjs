import {expect,test} from '@playwright/test';
import {fixtureUrl,acceptConfirmation} from './browser-helpers.mjs';

for(const bundled of [false,true]) for(const scenario of ['retry','failed-read-retry','replace-title','failed-read-replace-title']) test(`committed import pruning failure bundled=${bundled} scenario=${scenario}`,async({page})=>{
  await page.goto(fixtureUrl('assistant/basics',bundled?'&bundle=1':''));
  const panel=page.locator('extended-openai-management-panel');
  await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue('1200');
  await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'review-admin',is_admin:true}};});
  await expect.poll(()=>panel.evaluate(host=>!host._busy&&!host._eocCatalogBootstrap?.pending)).toBe(true);
  await panel.evaluate((host,failRead)=>{
    const send=host.hass.callWS.bind(host.hass);
    window.importRetentionWitness={imports:[],reads:0,previews:0,followupSaves:[],failRead};
    host.hass.callWS=async request=>{
      const w=window.importRetentionWitness;
      if(request.section==='configuration'&&request.action==='save') w.followupSaves.push(structuredClone(request));
      if(request.section==='configuration'&&request.action==='get'){
        w.reads++;
        if(w.imports.length&&w.failRead){w.failRead=false;throw {code:'offline',message:'Configuration refresh unavailable'};}
      }
      if(request.section==='configuration'&&request.action==='import_preview'){
        w.previews++;
        return {title:'Imported',summary:{model:'gpt-5-mini',tools:0,function_groups:0,speech_rules:0}};
      }
      if(request.section==='configuration'&&request.action==='import'){
        w.imports.push(structuredClone(request));
        const result=await send({...request,action:'save',title:'Imported',config:{usage_request_retention_days:0}});
        w.savedRevision=result.revision;
        if(w.imports.length===1) throw {code:'eoai_mutation_committed',message:'Configuration saved, but Usage retention could not be applied; retry saving retention to finish deletion.'};
        return {status:'updated',subentry_id:request.subentry_id,revision:result.revision};
      }
      return send(request);
    };
  },scenario.startsWith('failed-read'));
  await panel.locator('.agent-actions-menu > summary').click();
  await panel.locator('#import-agent').click();
  await panel.locator('input[name="import-mode"][value="current"]').check();
  await panel.locator('#import-document').fill('reviewed configuration with disabled request retention');
  await panel.locator('#import-preview').click();
  await expect(panel.locator('#import-apply')).toBeEnabled();
  await panel.locator('#import-apply').click();await acceptConfirmation(panel);
  await expect(panel.locator('#import-summary')).toContainText('may already have been applied');
  await expect.poll(()=>panel.evaluate(host=>host._eocImportPending)).toBe(false);
  if(scenario.endsWith('replace-title')){
    await panel.evaluate(host=>{
      const next=document.createElement('extended-openai-management-panel');
      next.hass=host.hass;host.remove();document.body.append(next);window.browserHarness.panel=next;
    });
    await expect.poll(()=>panel.evaluate(host=>!host._busy&&host._configData?.config?.usage_request_retention_days===0)).toBe(true);
    await panel.locator('[data-config="__title"]').fill('Unrelated title edit');
    await panel.locator('#save-config').click();
    await expect(panel.locator('#toast')).toContainText('Changes saved');
    const witness=await panel.evaluate(host=>({...window.importRetentionWitness,dirty:host._configDirty,pendingRetention:Boolean(host._eocPendingRetention)}));
    console.log(JSON.stringify({bundled,scenario,...witness}));
    expect(witness.followupSaves.at(-1).config).toEqual({usage_request_retention_days:0,usage_run_retention_days:30});
    return;
  }
  await panel.locator('#import-preview').click();
  await expect(panel.locator('#import-apply')).toBeEnabled();
  await panel.locator('#import-apply').click();await acceptConfirmation(panel);
  await expect.poll(()=>panel.evaluate(host=>host._eocImportPending)).toBe(false);
  const witness=await panel.evaluate(host=>({...window.importRetentionWitness,revision:host._configData.revision,toast:host._toastMessage,summary:host.shadowRoot.querySelector('#import-summary')?.textContent}));
  console.log(JSON.stringify({bundled,scenario,...witness}));
  await expect(panel.locator('#toast')).toContainText('Configuration imported');
});
