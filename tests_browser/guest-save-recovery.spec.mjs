import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

async function openGuest(page, bundled) {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#guest-web-search")).toBeAttached();
  await panel.evaluate(host => {host.hass={...host.hass,user:{id:"guest-save-owner",is_admin:true}};});
  await panel.locator("#guest-web-search").evaluate(input => {for(let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
  return {panel,errors};
}

async function holdReply(panel, action, reply) {
  await panel.evaluate((host,{action,reply}) => {
    const send=host.hass.callWS.bind(host.hass);
    window.guestSave={source:host,writes:[],notices:[]};
    const toast=host._toast.bind(host);
    host._toast=(message,warning) => {guestSave.notices.push({message,warning});toast(message,warning);};
    host.hass.callWS=async request => {
      const result=await send(request);
      if(request.section !== "guest_mode" || request.action !== action) return result;
      if(action === "update") result.status={...result.status,active_from:request.active_from || null,active_until:request.active_until || null,indefinite:request.indefinite};
      guestSave.writes.push(structuredClone(request));
      if(guestSave.writes.length !== 1) return result;
      guestSave.committed=true;
      await new Promise(resolve => {guestSave.release=resolve;});
      if(reply === "lost") throw {code:3,message:"Connection lost"};
      return reply === "malformed" ? {} : result;
    };
  },{action,reply});
}

async function replacePanel(panel) {
  await panel.evaluate(host => {
    const parent=host.parentElement, next=document.createElement("extended-openai-management-panel");
    next.hass=host.hass;host.remove();parent.append(next);browserHarness.panel=next;
  });
}

for(const bundled of [false,true]) for(const reply of ["malformed","lost","success"]) for(const detached of ["pending","settled"]) test(`newer Guest policy reversion survives ${reply} after panel replacement; bundle=${bundled}, detached=${detached}`,async ({page}) => {
  const {panel,errors}=await openGuest(page,bundled);
  await holdReply(panel,"save_policy",reply);
  await panel.locator("#guest-web-search").check();
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => guestSave.committed)).toBe(true);
  await panel.locator("#guest-web-search").uncheck();
  if(detached === "settled") {
    await page.evaluate(() => guestSave.release());
    await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/guest-mode").pending)).toBe(false);
  }
  await replacePanel(panel);
  await expect(panel.locator("#guest-web-search")).toBeAttached();
  if(detached === "pending") await page.evaluate(() => guestSave.release());
  await expect.poll(() => panel.evaluate(host => host._guestDraft.guest_web_search)).toBe(false);
  await expect(panel.locator("#save-page")).toBeEnabled();
  expect(await page.evaluate(() => guestSave.writes.length)).toBe(1);
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().guest.config.guest_web_search)).toBe(false);
  await expect(panel.locator("#save-page")).toHaveCount(0);
  expect(await page.evaluate(() => guestSave.writes.length)).toBe(2);
  await expectHarnessClean(page,errors);
});

for(const bundled of [false,true]) for(const action of ["update","disable"]) for(const reply of ["malformed","lost"]) test(`retired Guest ${action} ${reply} error is suppressed after A-B-A; bundle=${bundled}`,async ({page}) => {
  const {panel,errors}=await openGuest(page,bundled);
  await holdReply(panel,action,reply);
  await panel.locator("#guest-indefinite").check();
  if(action === "update") await panel.locator("#guest-now").click();
  else await panel.evaluate(host => {host._confirm=async () => true;void host._disableGuestMode();});
  await expect.poll(() => page.evaluate(() => guestSave.committed)).toBe(true);
  await panel.evaluate(host => {
    const user=host.hass.user;
    host.hass={...host.hass,user:{id:"guest-other-admin",is_admin:true}};
    host.hass={...host.hass,user};
  });
  await page.evaluate(() => guestSave.release());
  await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  expect(await page.evaluate(() => guestSave.notices)).toEqual([]);
  expect(await page.evaluate(() => guestSave.writes.length)).toBe(1);
  await expectHarnessClean(page,errors);
});

for(const bundled of [false,true]) test(`newer inactive Guest end survives successful acknowledgement and panel replacement; bundle=${bundled}`,async ({page}) => {
  const {panel,errors}=await openGuest(page,bundled);
  await panel.locator("#guest-indefinite").uncheck();
  await panel.locator("#guest-end").fill("2027-06-04T13:00");
  await panel.locator("#guest-indefinite").check();
  await holdReply(panel,"update","success");
  await panel.locator("#guest-now").click();
  await expect.poll(() => page.evaluate(() => guestSave.committed)).toBe(true);
  await panel.locator("#guest-indefinite").uncheck();
  await panel.locator("#guest-end").fill("2027-06-04T14:00");
  await panel.locator("#guest-indefinite").check();
  await replacePanel(panel);
  await expect(panel.locator("#guest-end")).toBeAttached();
  await page.evaluate(() => guestSave.release());
  await expect(panel.locator("#guest-end")).toHaveValue("2027-06-04T14:00");
  await panel.locator("#guest-indefinite").uncheck();
  await expect(panel.locator("#guest-end")).toBeEnabled();
  expect(await page.evaluate(() => guestSave.writes.length)).toBe(1);
  await expectHarnessClean(page,errors);
});
