import {expect,test} from "@playwright/test";
import {fixtureUrl,acceptConfirmation} from "./browser-helpers.mjs";
for(const bundled of [false,true]) test(`retired import recovers into an older loaded panel: bundle=${bundled}`,async({page})=>{
  await page.goto(fixtureUrl('assistant/basics',bundled?'&bundle=1':''));
  const source=page.locator('extended-openai-management-panel').first();
  await expect(source.locator('[data-config="max_tokens"]')).toHaveValue('1200');
  await source.evaluate(host=>{host.id='stale-source';host.hass={...host.hass,user:{id:'stale-admin',is_admin:true}};});
  await expect.poll(()=>source.evaluate(host=>!host._busy&&!host._eocCatalogBootstrap?.pending)).toBe(true);
  await source.evaluate(host=>{
    const target=document.createElement('extended-openai-management-panel');target.id='stale-target';target.hass=host.hass;
    document.body.append(target);browserHarness.panel=target;
  });
  const target=page.locator('#stale-target');
  await expect(target.locator('[data-config="max_tokens"]')).toHaveValue('1200');
  await target.locator('[data-config="max_tokens"]').fill('888');
  await source.evaluate(host=>{
    const send=host.hass.callWS.bind(host.hass);window.staleRecovery={committed:false,failReads:true};
    host.hass.callWS=async request=>{
      const w=window.staleRecovery;
      if(request.section==='configuration'&&request.action==='get'&&w.committed&&w.failReads)throw {code:'offline',message:'Refresh unavailable'};
      if(request.section==='configuration'&&request.action==='import_preview')return {title:'Imported',summary:{model:'gpt-5-mini',tools:0,function_groups:0,speech_rules:0}};
      if(request.section==='configuration'&&request.action==='import'){
        await send({...request,action:'save',title:'Imported',config:{usage_request_retention_days:0,max_tokens:1600}});
        w.committed=true;throw {code:'eoai_mutation_committed',message:'Retention deletion failed'};
      }
      return send(request);
    };
  });
  await source.locator('.agent-actions-menu > summary').click();await source.locator('#import-agent').click();
  await source.locator('input[name="import-mode"][value="current"]').check();
  await source.locator('#import-document').fill('reviewed imported configuration');await source.locator('#import-preview').click();
  await source.locator('#import-apply').click();await acceptConfirmation(source);
  await expect.poll(()=>source.evaluate(host=>host._eocImportPending)).toBe(false);
  await source.locator('#import-cancel').click();
  await target.evaluate(async host=>{document.querySelector('#stale-source').remove();staleRecovery.failReads=false;await host._loadSection(true);});
  await expect.poll(()=>target.evaluate(host=>host._draft.usage_request_retention_days)).toBe(0);
  await expect(target.locator('[data-config="max_tokens"]')).toHaveValue('888');
  expect(await target.evaluate(host=>host._configData.revision)).toBe(await page.evaluate(()=>browserHarness.getState().configuration.revision));
});
