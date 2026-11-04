import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

for(const bundled of [false,true]) for(const outcome of ["partial","lost","not committed"]) for(const newer of ["none","revert","inactive end"]) for(const read of ["section","timed"]) test(`interval read recovery: ${outcome}, ${newer}, ${read}, bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.clock.install({time:new Date("2026-11-04T01:00:00Z")});
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#guest-start")).toBeAttached();
  await panel.evaluate(async (host,outcome) => {
    host.hass={...host.hass,user:{id:"interval-read-owner",is_admin:true},config:{...host.hass.config,time_zone:"UTC"}};
    const send=host.hass.callWS.bind(host.hass);
    window.intervalRead={status:{active_from:null,active_until:null,indefinite:false},reads:0,writes:0};
    host.hass.callWS=async request => {
      if(request.section !== "guest_mode") return send(request);
      if(request.action === "get") {intervalRead.reads++;return {...await send(request),status:structuredClone(intervalRead.status)};}
      if(request.action !== "update") return send(request);
      intervalRead.writes++;
      if(outcome !== "not committed") {
        await send(request);
        const instant=key => request[key] ? request.local_times?.[key] ? `${request[key]}:00Z` : request[key] : null;
        intervalRead.status={active_from:instant("active_from"),active_until:instant("active_until"),indefinite:request.indefinite};
      }
      intervalRead.entered=true;await new Promise(resolve => {intervalRead.release=resolve;});
      if(outcome !== "partial") throw {code:3,message:"Connection lost"};
      return {status:{state:"active"}};
    };
    host._sectionCache.clear();await host._loadSection(true);
  },outcome);
  await panel.locator("#guest-start").fill("2027-06-04T12:00");
  await panel.locator("#guest-indefinite").uncheck();await panel.locator("#guest-end").fill("2027-06-04T13:00");
  if(newer === "inactive end") await panel.locator("#guest-indefinite").check();
  await panel.locator("#guest-update").click();
  await expect.poll(() => page.evaluate(() => intervalRead.entered)).toBe(true);
  if(newer === "revert") {await panel.locator("#guest-start").fill("");await panel.locator("#guest-end").fill("");}
  if(newer === "inactive end") {
    await panel.locator("#guest-indefinite").uncheck();await panel.locator("#guest-end").fill("2027-06-04T17:00");await panel.locator("#guest-indefinite").check();
  }
  await page.evaluate(() => intervalRead.release());
  await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").needsRead())).toBe(true);
  if(read === "section") await panel.locator('.subsection-nav button[data-subsection="guest-mode"]').click();
  else await page.clock.runFor(60_100);
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").needsRead())).toBe(false);
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").dirty())).toBe(outcome === "not committed" ? newer !== "revert" : newer !== "none");
  await expect(panel.locator("#guest-start")).toHaveValue(newer === "revert" ? "" : "2027-06-04T12:00");
  await expect(panel.locator("#guest-end")).toHaveValue(newer === "revert" ? "" : newer === "inactive end" ? "2027-06-04T17:00" : "2027-06-04T13:00");
  expect(await page.evaluate(() => intervalRead.writes)).toBe(1);
  const saved=await panel.evaluate(host => structuredClone(host._guestIntervalDraft.baseline));
  await panel.locator("#guest-indefinite").uncheck();await panel.locator("#guest-start").fill(saved.start);await panel.locator("#guest-end").fill(saved.end);
  if(saved.indefinite) await panel.locator("#guest-indefinite").check();
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").dirty())).toBe(false);
  await expectHarnessClean(page,errors);
});

for(const bundled of [false,true]) for(const outcome of ["partial","lost"]) for(const newer of [false,true]) test(`interval scope Discard adopts saved values after ${outcome}, newer end=${newer}, bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");await expect(panel.locator("#guest-start")).toBeAttached();
  await panel.evaluate(async (host,outcome) => {
    host.hass={...host.hass,user:{id:"interval-discard-owner",is_admin:true},config:{...host.hass.config,time_zone:"UTC"}};
    const send=host.hass.callWS.bind(host.hass);
    window.intervalDiscard={status:{active_from:"2027-06-04T12:00:00Z",active_until:"2027-06-04T15:00:00Z",indefinite:false},writes:0};
    host.hass.callWS=async request => {
      if(request.section !== "guest_mode") return send(request);
      if(request.action === "get") return {...await send(request),status:structuredClone(intervalDiscard.status)};
      if(request.action !== "update") return send(request);
      intervalDiscard.writes++;
      intervalDiscard.status.active_until=`${request.active_until}:00Z`;
      if(outcome === "partial") return {status:{state:"active"}};
      throw {code:3,message:"Connection lost"};
    };
    host._sectionCache.clear();await host._loadSection(true);
  },outcome);
  await panel.locator("#guest-end").fill("2027-06-04T14:00");await panel.locator("#guest-update").click();
  await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  await panel.evaluate(host => {host._unsavedState.scopes.get("guest-interval").discard();host._render();});
  await expect(panel.locator("#guest-end")).toHaveValue("2027-06-04T15:00");
  if(newer) await panel.locator("#guest-end").fill("2027-06-04T17:00");
  await panel.locator('.subsection-nav button[data-subsection="guest-mode"]').click();
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").needsRead())).toBe(false);
  await expect(panel.locator("#guest-end")).toHaveValue(newer ? "2027-06-04T17:00" : "2027-06-04T14:00");
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").dirty())).toBe(newer);
  expect(await page.evaluate(() => intervalDiscard.writes)).toBe(1);await expectHarnessClean(page,errors);
});

for(const bundled of [false,true]) for(const first of ["partial","lost"]) for(const field of ["start","end","indefinite"]) test(`a later unresolved interval ${field} reversion stays authored after ${first}; bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");await expect(panel.locator("#guest-start")).toBeAttached();
  await panel.evaluate(async (host,first) => {
    host.hass={...host.hass,user:{id:"interval-retry-owner",is_admin:true},config:{...host.hass.config,time_zone:"UTC"}};
    const send=host.hass.callWS.bind(host.hass);
    window.intervalRetry={status:{active_from:"2027-06-04T12:00:00Z",active_until:"2027-06-04T15:00:00Z",indefinite:false},writes:0};
    host.hass.callWS=async request => {
      if(request.section !== "guest_mode") return send(request);
      if(request.action === "get") return {...await send(request),status:structuredClone(intervalRetry.status)};
      if(request.action !== "update") return send(request);
      if(++intervalRetry.writes === 1) {
        const instant=key => request.local_times?.[key] ? `${request[key]}:00Z` : request[key];
        intervalRetry.status={active_from:instant("active_from"),active_until:request.indefinite ? null : instant("active_until"),indefinite:request.indefinite};
        if(first === "partial") return {status:{state:"active"}};
      }
      throw {code:3,message:"Connection lost"};
    };
    host._sectionCache.clear();await host._loadSection(true);
  },first);
  const control=panel.locator(`#guest-${field}`),original=field === "start" ? "2027-06-04T12:00" : "2027-06-04T15:00";
  if(field === "indefinite") await control.check();else await control.fill("2027-06-04T14:00");
  await panel.locator("#guest-update").click();await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  if(field === "indefinite") await control.uncheck();else await control.fill(original);
  await panel.locator("#guest-update").click();await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  await panel.locator('.subsection-nav button[data-subsection="guest-mode"]').click();
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").needsRead())).toBe(false);
  if(field === "indefinite") await expect(control).not.toBeChecked();else await expect(control).toHaveValue(original);
  expect(await panel.evaluate(host => host._unsavedState.scopes.get("guest-interval").dirty())).toBe(true);
  expect(await page.evaluate(() => intervalRetry.writes)).toBe(2);await expectHarnessClean(page,errors);
});
