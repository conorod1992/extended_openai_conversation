import {test,expect} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test.use({timezoneId:"America/Los_Angeles"});

for(const clock of ["2026-11-01T12:00:00Z","2026-11-07T12:00:00Z"]) for(const bundled of [false,true]) for(const zone of ["UTC","Pacific/Honolulu"]) for(const choice of ["expired","future","indefinite","authored past","corrected end","future schedule","missing end","invalid end"]) test(`Guest Activate now: ${choice}, HA=${zone}, bundle=${bundled}, clock=${clock}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.clock.setFixedTime(new Date(clock));
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#guest-now")).toBeVisible();
  await panel.evaluate(async (host,{zone,choice}) => {
    host.hass={...host.hass,user:{id:"expired-interval-admin",is_admin:true},config:{...host.hass.config,time_zone:zone}};
    const send=host.hass.callWS.bind(host.hass);
    window.expiredActivation={writes:[],status:{active_from:choice === "future schedule" ? "2026-11-06T10:00:00Z" : "2026-11-03T10:00:00Z",active_until:choice === "future schedule" ? "2026-11-07T15:00:00Z" : choice === "future" ? "2026-11-04T15:00:00Z" : "2026-11-03T15:00:00Z",indefinite:false}};
    host.hass.callWS=async request => {
      if(request.section !== "guest_mode") return send(request);
      if(request.action === "get") return {...await send(request),status:structuredClone(expiredActivation.status)};
      if(request.action !== "update") return send(request);
      expiredActivation.writes.push(structuredClone(request));
      if(!request.indefinite && (choice === "expired" || choice === "authored past")) throw {code:"invalid_request",message:"active_until must be later than active_from"};
      expiredActivation.status={active_from:"2026-11-04T12:00:00+00:00",active_until:request.active_until || null,indefinite:request.indefinite};
      return {status:structuredClone(expiredActivation.status)};
    };
    host._sectionCache.clear();await host._loadSection(true);
  },{zone,choice});
  const saved=await page.evaluate(() => structuredClone(expiredActivation.status));
  const initial=await panel.locator("#guest-end").inputValue();
  if(choice === "indefinite") await panel.locator("#guest-indefinite").check();
  if(choice === "authored past") await panel.locator("#guest-end").fill("2026-11-02T01:00");
  if(choice === "corrected end") await panel.locator("#guest-end").fill("2026-11-05T14:00");
  if(choice === "missing end") await panel.locator("#guest-end").fill("");
  if(choice === "invalid end") await panel.locator("#guest-end").evaluate(input => {input.checkValidity=() => false;});
  await panel.locator("#guest-now").click();
  await expect.poll(() => panel.evaluate(host => Boolean(host._guestOperation))).toBe(false);
  const writes=await page.evaluate(() => expiredActivation.writes);
  for(const request of writes) {
    expect(Object.hasOwn(request,"active_from")).toBe(false);
    expect(Object.hasOwn(request.local_times,"active_from")).toBe(false);
  }
  if(choice === "missing end" || choice === "invalid end") {
    expect(writes).toEqual([]);
    await expect(panel).toContainText("Choose an end time");
    expect(await page.evaluate(() => expiredActivation.status)).toEqual(saved);
  } else if(choice === "expired" || choice === "authored past") {
    expect(writes).toHaveLength(1);
    expect(await page.evaluate(() => expiredActivation.status)).toEqual(saved);
    await expect(panel).toContainText("active_until must be later than active_from");
    await expect(panel.locator("#guest-end")).toHaveValue(choice === "expired" ? initial : "2026-11-02T01:00");
    await panel.locator("#guest-indefinite").check();await panel.locator("#guest-now").click();
    await expect.poll(() => page.evaluate(() => expiredActivation.writes.length)).toBe(2);
    expect(await page.evaluate(() => expiredActivation.writes[1].indefinite)).toBe(true);
  } else {
    expect(writes).toHaveLength(1);
    expect(await page.evaluate(() => expiredActivation.status.active_from)).toBe("2026-11-04T12:00:00+00:00");
    if(choice === "indefinite") expect(writes[0].active_until).toBeUndefined();
    if(choice === "future") expect(writes[0].active_until).toBe("2026-11-04T15:00:00Z");
    if(choice === "corrected end") {
      expect(writes[0].active_until).toBe("2026-11-05T14:00");
      expect(writes[0].local_times.active_until).toBe("2026-11-05T14:00");
    }
  }
  await expectHarnessClean(page,errors);
});
