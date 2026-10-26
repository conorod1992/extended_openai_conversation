import {expect,test} from "@playwright/test";
import {expectHarnessClean,fixtureUrl,trackPageErrors} from "./browser-helpers.mjs";

async function openVoice(page,bundled) {
  await page.goto(fixtureUrl("assistant/voice",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._selectedAgent()) && !host._busy)).toBe(true);
  return panel;
}

for(const bundled of [false,true]) {
  for(const propertyFirst of [false,true]) test(`cold catalog schedules once with hass ${propertyFirst ? "before" : "after"} append (${bundled ? "bundle" : "source"})`,async ({page}) => {
    const errors=trackPageErrors(page),panel=await openVoice(page,bundled);
    await panel.evaluate((old,propertyFirst) => {
      const parent=old.parentElement,original=old.hass.callWS.bind(old.hass);
      old.remove();window.coldCatalog={reads:0};
      const hass={...old.hass,user:{id:"admin",is_admin:true},callWS:request => {
        if(request.action === "agents" && !request.section) coldCatalog.reads++;
        return original(request);
      }};
      const next=document.createElement("extended-openai-management-panel");
      if(propertyFirst) {next.hass=hass;parent.append(next);} else {parent.append(next);next.hass=hass;}
    },propertyFirst);
    await expect.poll(() => panel.evaluate(host => host._selectedAgent?.()?.subentry_id)).toBe("agent-1");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    await panel.evaluate(host => {host.hass={...host.hass};host.hass={...host.hass};});
    expect(await page.evaluate(() => coldCatalog.reads)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`cold user availability starts a new read and rejects the unknown-user result (${bundled ? "bundle" : "source"})`,async ({page}) => {
    const errors=trackPageErrors(page),panel=await openVoice(page,bundled);
    await panel.evaluate(old => {
      const parent=old.parentElement,original=old.hass.callWS.bind(old.hass);
      old.remove();window.coldCatalog={releases:[],reads:0};
      const hass={...old.hass,user:undefined,callWS:async request => {
        const result=await original(request);
        if(request.action === "agents" && !request.section) {
          const index=coldCatalog.reads++;
          await new Promise(resolve => {coldCatalog.releases[index]=resolve;});
        }
        return result;
      }};
      const next=document.createElement("extended-openai-management-panel");parent.append(next);next.hass=hass;
    });
    await expect.poll(() => page.evaluate(() => coldCatalog.reads)).toBe(1);
    await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};host.hass={...host.hass};});
    await expect.poll(() => page.evaluate(() => coldCatalog.reads)).toBe(2);
    await page.evaluate(() => coldCatalog.releases[1]());
    await expect.poll(() => panel.evaluate(host => host._selectedAgent()?.subentry_id)).toBe("agent-1");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    await panel.evaluate(host => {coldCatalog.accepted=host._data;});
    await page.evaluate(() => coldCatalog.releases[0]());
    await panel.evaluate(async host => {await new Promise(requestAnimationFrame);coldCatalog.same=host._data===coldCatalog.accepted;});
    expect(await page.evaluate(() => ({reads:coldCatalog.reads,same:coldCatalog.same}))).toEqual({reads:2,same:true});
    await expectHarnessClean(page,errors);
  });

  test(`failed cold read does not retry on hass updates but reconnect starts a fresh attempt (${bundled ? "bundle" : "source"})`,async ({page}) => {
    const errors=trackPageErrors(page),panel=await openVoice(page,bundled);
    await panel.evaluate(old => {
      const parent=old.parentElement,original=old.hass.callWS.bind(old.hass);
      old.remove();window.coldCatalog={reads:0};
      const hass={...old.hass,user:{id:"admin",is_admin:true},callWS:request => {
        if(request.action === "agents" && !request.section && ++coldCatalog.reads === 1) throw new Error("Controlled cold catalog failure");
        return original(request);
      }};
      const next=document.createElement("extended-openai-management-panel");parent.append(next);next.hass=hass;
    });
    await expect.poll(() => panel.evaluate(host => host._error)).toBe("Controlled cold catalog failure");
    await panel.evaluate(async host => {for(let index=0;index<3;index++){host.hass={...host.hass};await new Promise(requestAnimationFrame);}});
    expect(await page.evaluate(() => coldCatalog.reads)).toBe(1);
    await panel.evaluate(host => {const parent=host.parentElement;host.remove();parent.append(host);});
    await expect.poll(() => panel.evaluate(host => host._selectedAgent()?.subentry_id)).toBe("agent-1");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await page.evaluate(() => coldCatalog.reads)).toBe(2);
    await expectHarnessClean(page,errors);
  });

  test(`custom-element upgrade replays hass without duplicate cold reads (${bundled ? "bundle" : "source"})`,async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/voice",`&predefine=1${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect.poll(() => panel.evaluate(host => host._selectedAgent?.()?.subentry_id)).toBe("agent-1");
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await page.evaluate(() => browserHarness.calls.filter(request => request.action === "agents" && !request.section).length)).toBe(1);
    await expectHarnessClean(page,errors);
  });
}
