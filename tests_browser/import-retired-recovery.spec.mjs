import {expect,test} from "@playwright/test";
import {fixtureUrl,acceptConfirmation} from "./browser-helpers.mjs";
for(const bundled of [false,true]) for(const draft of ["clean","retired","newer"]) test(`retired policy does not conflict with new draft: bundle=${bundled}, draft=${draft}`,async({page})=>{
  await page.goto(fixtureUrl('assistant/basics',bundled?'&bundle=1':''));
  const panel=page.locator('extended-openai-management-panel');
  await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue('1200');
  await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'review-admin',is_admin:true}};});
  await expect.poll(()=>panel.evaluate(host=>!host._busy&&!host._eocCatalogBootstrap?.pending)).toBe(true);
  await panel.evaluate(host=>host._navigate('usage-maintenance','retention'));
  await expect(panel.locator('#config-usage_request_retention_days')).toHaveValue('30');
  if(draft !== "clean") await panel.locator('#config-usage_request_retention_days').selectOption('7');
  await panel.evaluate(host=>host._navigate('assistant','basics'));
  await panel.evaluate(host=>{
    const send=host.hass.callWS.bind(host.hass);
    window.reviewRecovery={imported:false,failReads:true,reads:0};
    host.hass.callWS=async request=>{
      const w=window.reviewRecovery;
      if(request.section==='configuration'&&request.action==='get'&&w.imported){w.reads++;if(w.failReads)throw {code:'offline',message:'Full configuration read unavailable'};}
      if(request.section==='configuration'&&request.action==='import_preview')return {title:'Imported',summary:{model:'gpt-5-mini',tools:0,function_groups:0,speech_rules:0}};
      if(request.section==='configuration'&&request.action==='import'){
        await send({...request,action:'save',title:'Imported',config:{usage_request_retention_days:0}});
        w.imported=true;
        throw {code:'eoai_mutation_committed',message:'Configuration saved, but Usage retention could not be applied; retry saving retention to finish deletion.'};
      }
      return send(request);
    };
  });
  await panel.locator('.agent-actions-menu > summary').click();await panel.locator('#import-agent').click();
  await panel.locator('input[name="import-mode"][value="current"]').check();
  await panel.locator('#import-document').fill('reviewed imported policy with disabled retention');
  await panel.locator('#import-preview').click();await expect(panel.locator('#import-apply')).toBeEnabled();
  await panel.locator('#import-apply').click();await acceptConfirmation(panel);
  await expect.poll(()=>panel.evaluate(host=>host._eocImportPending)).toBe(false);
  await panel.locator('#import-cancel').click();
  if(draft === "newer") await panel.locator('[data-config="max_tokens"]').fill('999');
  await panel.evaluate(host=>host._navigate('usage-maintenance','retention'));
  await panel.evaluate(host=>{
    window.retiredRecoverySource=host;
    const next=document.createElement('extended-openai-management-panel');
    next.hass=host.hass;host.remove();document.body.append(next);browserHarness.panel=next;
  });
  await expect(panel.getByText('Unsaved configuration is retained. Refresh this section to retry loading the complete settings.',{exact:true})).toBeVisible();
  await expect(panel.locator('#config-usage_request_retention_days')).toHaveValue('0');
  await panel.locator('#config-usage_request_retention_days').selectOption('30');
  await page.evaluate(()=>{reviewRecovery.failReads=false;});
  await panel.locator('.top-nav button[data-page="assistant"]').click();
  const witness=await panel.evaluate(host=>({prior:retiredRecoverySource._eocRestoredConfiguration?.draft.usage_request_retention_days,
    draft:host._draft.usage_request_retention_days,pending:host._eocPendingRetention?.fields,
    copy:host.shadowRoot.querySelector('dialog[data-eoc-recovered-editor] textarea')?.value,reads:reviewRecovery.reads}));
  await expect(panel.locator('dialog[data-eoc-recovered-editor][open]')).toHaveCount(0);
  expect(witness.draft).toBe(30);expect(witness.pending).toContain('usage_request_retention_days');
  if(draft === "newer") await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue('999');
  await panel.locator('#save-config').click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.usage_request_retention_days)).toBe(30);
  const writes=await page.evaluate(()=>browserHarness.calls.filter(call=>call.section==='configuration'&&call.action==='save'));
  expect(writes.at(-1).config.usage_request_retention_days).toBe(30);
  expect(writes.at(-1).config.usage_run_retention_days).toBe(30);
  if(draft === "newer") expect(writes.at(-1).config.max_tokens).toBe(999);
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
});
