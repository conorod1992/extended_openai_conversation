import {expect,test} from "@playwright/test";
import {expectHarnessClean,fixtureUrl,trackPageErrors} from "./browser-helpers.mjs";

async function mountCold(page,{bundled,stored=true,retain=false,user=true,holdCatalog=false}) {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
  await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
  if(retain) {
    await panel.locator('[data-config="max_tokens"]').fill("1777");
    await panel.locator('[data-config="max_tokens"]').press("Tab");
    await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(true);
  }
  await panel.evaluate((old,{stored,user,holdCatalog}) => {
    const parent=old.parentElement,original=old.hass.callWS.bind(old.hass);
    old.remove();
    if(!stored){localStorage.removeItem("extended-openai-agent");localStorage.removeItem("extended-openai-agent-entry");}
    window.coldPhase={catalogs:0,configs:0,users:[],parent,holdCatalog};
    const next=document.createElement("extended-openai-management-panel");
    coldPhase.host=next;
    const hass={...old.hass,user:user ? {id:"admin",is_admin:true} : undefined,callWS:async request => {
      const catalog=request.action === "agents" && !request.section ? ++coldPhase.catalogs : 0;
      const config=request.section === "configuration" && request.action === "get";
      const index=config ? ++coldPhase.configs : 0;
      if(config) coldPhase.users.push(next.hass?.user?.id || null);
      if(config && index > 1 && coldPhase.reject) throw new Error("Controlled current-user read failure");
      const result=await original(request);
      if(catalog === 2 && coldPhase.holdCatalog) await new Promise(resolve => {coldPhase.releaseCatalog=resolve;});
      if(index === 2 && coldPhase.holdSecond) {
        const obsolete=structuredClone(result);obsolete.config.max_tokens=8888;
        await new Promise(resolve => {coldPhase.releaseSecond=resolve;});
        return obsolete;
      }
      if(index === 1) {
        const obsolete=structuredClone(result);obsolete.config.max_tokens=9999;
        await new Promise(resolve => {coldPhase.release=resolve;});
        coldPhase.firstReturned=true;
        return obsolete;
      }
      return result;
    }};
    parent.append(next);next.hass=hass;
  },{stored,user,holdCatalog});
  await expect.poll(() => panel.evaluate(host => Boolean(host._data)&&Boolean(coldPhase.release))).toBe(true);
  return {panel,errors};
}

async function releaseOld(page) {
  await page.evaluate(() => coldPhase.release());
  await expect.poll(() => page.evaluate(() => coldPhase.firstReturned)).toBe(true);
  await page.evaluate(() => new Promise(requestAnimationFrame));
}

async function expectCurrent(page,panel,value="1200") {
  await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue(value);
  await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
  expect(await page.evaluate(() => coldPhase.catalogs)).toBe(2);
}

for(const bundled of [false,true]) {
  const mode=bundled ? "bundle" : "source";
  for(const stored of [false,true]) test(`cold page resolves user after catalogue publication, stored=${stored} (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,stored,user:false});
    await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await expectCurrent(page,panel);
    await releaseOld(page);
    await expectCurrent(page,panel);
    expect(await page.evaluate(() => coldPhase.users)).toEqual([null,"admin"]);
    await expectHarnessClean(page,errors);
  });

  for(const stored of [false,true]) for(const settleDetached of [false,true]) test(`cold page reconnect retires old read, stored=${stored}, settleDetached=${settleDetached} (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,stored});
    await panel.evaluate(host => host.remove());
    if(settleDetached) await releaseOld(page);
    await page.evaluate(() => coldPhase.parent.append(coldPhase.host));
    await expectCurrent(page,panel);
    if(!settleDetached) await releaseOld(page);
    await expectCurrent(page,panel);
    expect(await page.evaluate(() => coldPhase.configs)).toBe(2);
    await expectHarnessClean(page,errors);
  });

  test(`late cold user restores the matching retained draft (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,user:false,retain:true});
    await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await expectCurrent(page,panel,"1777");
    await releaseOld(page);
    await expectCurrent(page,panel,"1777");
    expect(await panel.evaluate(host => ({dirty:host._configDirty,baseline:host._configData.config.max_tokens}))).toEqual({dirty:true,baseline:1200});
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
    const writes=await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
    expect(writes).toHaveLength(1);expect(writes[0].config.max_tokens).toBe(1777);
    await expectHarnessClean(page,errors);
  });

  test(`old cold completion cannot settle its still-pending replacement (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,user:false});
    await panel.evaluate(host => {coldPhase.holdSecond=true;host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await expect.poll(() => page.evaluate(() => Boolean(coldPhase.releaseSecond))).toBe(true);
    await releaseOld(page);
    await panel.evaluate(host => {const parent=host.parentElement;host.remove();parent.append(host);});
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await page.evaluate(() => ({catalogs:coldPhase.catalogs,configs:coldPhase.configs}))).toEqual({catalogs:3,configs:3});
    await page.evaluate(async () => {coldPhase.releaseSecond();await new Promise(requestAnimationFrame);});
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await expectHarnessClean(page,errors);
  });

  for(const change of ["navigation","assistant"]) test(`replacement cold catalogue respects later ${change} (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,user:false,holdCatalog:true});
    await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await expect.poll(() => page.evaluate(() => Boolean(coldPhase.releaseCatalog))).toBe(true);
    // The first page is still loading, so use browser history rather than
    // controls that have not rendered yet. Guide exposes the normal picker.
    await page.evaluate(() => {
      history.replaceState({},"","/extended-openai/guide");
      history.pushState({},"","/extended-openai/assistant/basics");
    });
    await page.goBack();
    await expect.poll(() => panel.evaluate(host => host._page)).toBe("guide");
    if(change === "assistant") {
      await panel.locator("#agent").selectOption("scale-agent-1");
      await panel.getByRole("button",{name:"Assistant",exact:true}).click();
      await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
      await panel.locator('[data-config="max_tokens"]').fill("1777");
      await panel.locator('[data-config="max_tokens"]').press("Tab");
    }
    await page.evaluate(() => coldPhase.releaseCatalog());
    await releaseOld(page);
    if(change === "navigation") {
      expect(await panel.evaluate(host => host._page)).toBe("guide");
      await page.goForward();
      await expectCurrent(page,panel);
    } else {
      await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
      await expectCurrent(page,panel,"1777");
      expect(await panel.evaluate(host => host._configDirty)).toBe(true);
      await panel.locator("#save-config").click();
      await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
      const writes=await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save"));
      expect(writes).toHaveLength(1);expect(writes[0].subentry_id).toBe("scale-agent-1");
    }
    await expectHarnessClean(page,errors);
  });

  test(`failed resolved-user cold read keeps bounded retries and explicit recovery (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,user:false});
    await panel.evaluate(host => {coldPhase.reject=true;host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await expect.poll(() => panel.evaluate(host => host._error)).toContain("Controlled current-user read failure");
    await releaseOld(page);
    const before=await page.evaluate(() => ({catalogs:coldPhase.catalogs,configs:coldPhase.configs}));
    await panel.evaluate(async host => {for(let i=0;i<3;i++){host.hass={...host.hass};await new Promise(requestAnimationFrame);}});
    expect(await page.evaluate(() => ({catalogs:coldPhase.catalogs,configs:coldPhase.configs}))).toEqual(before);
    await panel.evaluate(host => {coldPhase.reject=false;return host._loadSection(true);});
    await expectCurrent(page,panel);
    await expectHarnessClean(page,errors);
  });

  test(`settled cold panel keeps an authored draft when user metadata becomes available (${mode})`,async ({page}) => {
    const {panel,errors}=await mountCold(page,{bundled,user:false});
    await releaseOld(page);
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("9999");
    await panel.locator('[data-config="max_tokens"]').fill("1777");
    await panel.locator('[data-config="max_tokens"]').press("Tab");
    await panel.evaluate(async host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};await new Promise(requestAnimationFrame);});
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1777");
    expect(await page.evaluate(() => ({catalogs:coldPhase.catalogs,configs:coldPhase.configs}))).toEqual({catalogs:1,configs:1});
    expect(await panel.evaluate(host => host._configDirty)).toBe(true);
    await expectHarnessClean(page,errors);
  });
}
