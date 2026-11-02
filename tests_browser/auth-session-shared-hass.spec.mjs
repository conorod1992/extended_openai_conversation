import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) {
  test(`an account change removes the previous owner's presentation and preserves their draft (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/prompt-context", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="prompt"]')).toBeVisible();
    await panel.evaluate(host => { host.hass = {...host.hass, user:{id:"presentation-owner", is_admin:true}}; });
    await panel.locator('[data-config="prompt"]').fill("Private authored prompt belonging to the original account");
    await panel.evaluate(host => {
      window.authoredPresentation = {hass:host.hass, draft:host._draft, owner:host.hass.user.id};
      host.hass.user.id = "restricted-other-account";
      host.hass.user.is_admin = false;
      host.hass = host.hass;
    });
    await expect(panel.getByRole("button", {name:"Reload settings", exact:true})).toBeVisible();
    await expect(panel.locator('[data-config="prompt"]')).toHaveCount(0);
    expect(await panel.evaluate(host => host._canAccessView("assistant", "basics"))).toBe(false);
    expect(await panel.evaluate(host => host.shadowRoot.textContent)).not.toContain("Private authored prompt");
    expect(await panel.evaluate(async host => {
      try { await host._call("configuration", "get"); return "unexpected read"; }
      catch (error) { return error.message; }
    })).toMatch(/account changed/i);
    await panel.evaluate(host => {
      host.hass.user.id = authoredPresentation.owner;
      host.hass.user.is_admin = true;
      host.hass = host.hass;
    });
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Private authored prompt belonging to the original account");
    expect(await panel.evaluate(host => host._draft === authoredPresentation.draft)).toBe(true);
    await expectHarnessClean(page, errors);
  });
}

for (const bundled of [false, true]) for (const change of ["mutable user id ABA", "mutable logout return"]) {
  test(`shared hass ${change} retires native Rule lookup publication (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("button", {name:"Create rule", exact:true}).first()).toBeVisible();
    await panel.evaluate(host => {
      host.hass = {...host.hass, user:{id:"shared-hass-owner-A", is_admin:true}};
      host.hass = host.hass;
      const original = host.hass.callWS.bind(host.hass);
      const state = window.sharedHassOwner = {requests:[], toasts:[]};
      host._toast = (...args) => state.toasts.push(args);
      host.hass.callWS = request => {
        if (!request.type?.endsWith("/model_catalog") || request.action !== "lookup") return original(request);
        return new Promise((resolve, reject) => state.requests.push({request:structuredClone(request), finish:async failed => {
          if (failed) reject(Error("Retired shared-object lookup failed"));
          else resolve({...await original(request), reasoning_effort_options:["retired-only"]});
        }}));
      };
    });
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill("Retained draft across a shared hass mutation");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("held-shared-object-model");
    await expect.poll(() => page.evaluate(() => sharedHassOwner.requests.length)).toBe(1);
    await panel.evaluate((host, change) => {
      const same = host.hass, user = same.user, originalId = user.id;
      sharedHassOwner.payload = same;
      sharedHassOwner.result = host._result;
      sharedHassOwner.dialog = host.shadowRoot.querySelector("#rule-dialog");
      sharedHassOwner.selector = host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector");
      if (change === "mutable user id ABA") same.user.id = "shared-hass-owner-B";
      else delete same.user;
      host.hass = same;
      if (change === "mutable user id ABA") same.user.id = originalId;
      else same.user = user;
      host.hass = same;
      sharedHassOwner.before = {
        choices:host.shadowRoot.querySelector("#rule-reasoning").innerHTML,
        catalog:structuredClone(host._modelCatalogData || null),
        error:host.shadowRoot.querySelector("#rule-error").textContent,
      };
    }, change);
    await page.evaluate(async failed => {
      await sharedHassOwner.requests[0].finish(failed);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, change === "mutable logout return");
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#rule-name")).toHaveValue("Retained draft across a shared hass mutation");
    await expect(panel.locator("#rule-model")).toHaveValue("held-shared-object-model");
    expect(await panel.evaluate(host => ({
      choices:host.shadowRoot.querySelector("#rule-reasoning").innerHTML,
      catalog:host._modelCatalogData || null,
      error:host.shadowRoot.querySelector("#rule-error").textContent,
    }))).toEqual(await page.evaluate(() => sharedHassOwner.before));
    expect(await panel.evaluate(host => host.hass === sharedHassOwner.payload && host._result === sharedHassOwner.result
      && host.shadowRoot.querySelector("#rule-dialog") === sharedHassOwner.dialog
      && host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector") === sharedHassOwner.selector)).toBe(true);
    expect(await page.evaluate(() => sharedHassOwner.toasts)).toEqual([]);
    expect(await page.evaluate(() => sharedHassOwner.requests.length)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false,true]) for(const aba of [false,true]) test(`late sharing export owner publication bundle=${bundle} aba=${aba}`,async({page})=>{
  await page.goto(`/tests_browser/fixture.html?route=usage-maintenance/backup-restore${bundle?'&bundle=1':''}`);
  const panel=page.locator('extended-openai-management-panel');
  await expect(panel.locator('#create-backup-transfer')).toBeVisible();
  await panel.evaluate(async host=>{
    host.hass={...host.hass,user:{id:'original-export-owner',is_admin:true}};
    const saved=await host._call('configuration','get');
    await host._call('configuration','save',{revision:saved.revision,config:{prompt:'OWNER_PRIVATE_CUSTOM_INSTRUCTION_V2'}});
    const call=host.hass.callWS.bind(host.hass);
    window.heldOwnerExport={calls:0,button:host.shadowRoot.querySelector('#create-backup-transfer')};
    host.hass.callWS=async message=>{
      const result=await call(message);
      if(message.action!=='setup_export')return result;
      heldOwnerExport.calls++;
      heldOwnerExport.result=result;
      return new Promise(resolve=>heldOwnerExport.release=()=>resolve(result));
    };
  });
  await panel.locator('#transfer-reviewed-custom').check();
  await panel.locator('#create-backup-transfer').click();
  await expect.poll(()=>page.evaluate(()=>heldOwnerExport.calls)).toBe(1);
  await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'replacement-account',is_admin:false}};});
  await expect(panel.getByRole('button',{name:'Reload settings',exact:true})).toBeVisible();
  await expect(panel.locator('[data-artifact]')).toHaveCount(0);
  if(aba)await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'original-export-owner',is_admin:true}};});
  await page.evaluate(()=>heldOwnerExport.release());
  await expect.poll(()=>page.evaluate(()=>heldOwnerExport.button.disabled)).toBe(false);
  await expect(panel.locator('[data-artifact]')).toHaveCount(0);

});

for (const bundle of [false,true]) for(const aba of [false,true]) test(`late full private backup owner download bundle=${bundle} aba=${aba}`,async({page})=>{
  await page.goto(`/tests_browser/fixture.html?route=usage-maintenance/backup-restore${bundle?'&bundle=1':''}`);
  const panel=page.locator('extended-openai-management-panel');
  await expect(panel.locator('#create-backup-transfer')).toBeVisible();
  await panel.evaluate(async host=>{
    host.hass={...host.hass,user:{id:'original-export-owner',is_admin:true}};
    const saved=await host._call('configuration','get');
    await host._call('configuration','save',{revision:saved.revision,config:{prompt:'OWNER_PRIVATE_FULL_BACKUP_V2'}});
    const call=host.hass.callWS.bind(host.hass);
    window.heldOwnerExport={calls:0,button:host.shadowRoot.querySelector('#create-backup-transfer')};
    host.hass.callWS=async message=>{
      const result=await call(message);
      if(message.action!=='export_chunk')return result;
      heldOwnerExport.calls++;
      return new Promise(resolve=>heldOwnerExport.release=()=>resolve(result));
    };
  });
  await panel.locator('#transfer-export-mode').selectOption('full');
  await panel.locator('#create-backup-transfer').click();
  await expect.poll(()=>page.evaluate(()=>heldOwnerExport.calls)).toBe(1);
  await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'replacement-account',is_admin:false}};});
  await expect(panel.getByRole('button',{name:'Reload settings',exact:true})).toBeVisible();
  if(aba)await panel.evaluate(host=>{host.hass={...host.hass,user:{id:'original-export-owner',is_admin:true}};});
  const downloads=[];page.on('download',download=>downloads.push(download));
  await page.evaluate(()=>heldOwnerExport.release());
  await expect.poll(()=>page.evaluate(()=>heldOwnerExport.button.disabled)).toBe(false);
  expect(downloads).toHaveLength(0);
});

for(const bundle of [false,true])test(`open sharing dialog retires across account replacement bundle=${bundle}`,async({page})=>{
 await page.goto(fixtureUrl("usage-maintenance/backup-restore",bundle?"&bundle=1":""));
 const panel=page.locator("extended-openai-management-panel");
 await expect(panel.locator("#create-backup-transfer")).toBeVisible();
 await panel.evaluate(host=>{host.hass={...host.hass,user:{id:"original-owner",is_admin:true}};});
 await panel.locator("#create-backup-transfer").click();
 await expect(panel.locator("[data-artifact]")).toBeVisible();
 await panel.evaluate(host=>{host.hass={...host.hass,user:{id:"replacement",is_admin:false}};});
 await expect(panel.locator("[data-artifact]")).toHaveCount(0);
 await panel.evaluate(host=>{host.hass={...host.hass,user:{id:"original-owner",is_admin:true}};});
 await expect(panel.locator("[data-artifact]")).toHaveCount(0);
});

for (const bundle of [false, true]) for (const aba of [false, true]) test(`debug queue retires account-owned sends bundle=${bundle} aba=${aba}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/request-debug", bundle ? "&bundle=1" : ""));
  const debug = page.locator("extended-openai-debug-panel");
  await expect(debug.locator("#enabled")).toBeEnabled();
  await debug.evaluate(async host => {
    host.hass = {...host._hass, user:{id:"original-debug-account", is_admin:true}};
    const call = host._hass.callWS.bind(host._hass);
    window.debugQueueOwner = {calls:[], originalId:host._hass.user.id};
    host._hass.callWS = async message => {
      const result = await call(message);
      if (message.action !== "configure") return result;
      debugQueueOwner.calls.push(message);
      return new Promise(resolve => { debugQueueOwner.release = () => resolve(result); });
    };
    debugQueueOwner.first = host._configure({enabled:true});
  });
  await expect.poll(() => page.evaluate(() => debugQueueOwner.calls.length)).toBe(1);
  await debug.evaluate((host, aba) => {
    debugQueueOwner.second = host._configure({enabled:false});
    const same = host._hass;
    same.user.id = "replacement-debug-account";
    host.hass = same;
    if (aba) { same.user.id = debugQueueOwner.originalId; host.hass = same; }
  }, aba);
  await page.evaluate(async () => {
    debugQueueOwner.release();
    await Promise.all([debugQueueOwner.first, debugQueueOwner.second]);
  });
  expect(await page.evaluate(() => debugQueueOwner.calls.map(message => message.enabled))).toEqual([true]);
  expect(await page.evaluate(() => browserHarness.getState().requestDebug.enabled)).toBe(true);
  await expectHarnessClean(page, errors);
});
