import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

for(const bundled of [false,true]) for(const kind of ["function","connection"]) test(`competing policy read recovers after ${kind} reconnect; bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel"),mode='[data-guest-mode="guest_function_policy"]';
  await expect(panel.locator(mode)).toBeVisible();
  await panel.evaluate((host,kind) => {
    host.hass={...host.hass,user:{id:"policy-reconnect-owner",is_admin:true}};
    const send=host.hass.callWS.bind(host.hass);
    window.policyReconnect={source:host,connected:true,connection:{connected:true},writes:0,reads:0};
    const call=async request => {
      if(request.section === "guest_mode" && request.action === "get") policyReconnect.reads++;
      if(!policyReconnect.connected) throw Error("Connection is closed");
      const result=await send(request);
      if(request.section === "guest_mode" && request.action === "save_policy") {
        policyReconnect.writes++;policyReconnect.committed=true;
        await new Promise(resolve => {policyReconnect.release=resolve;});
        policyReconnect.connected=false;policyReconnect.connection.connected=false;
        throw {code:3,message:"Connection lost"};
      }
      return result;
    };
    host.hass={...host.hass,callWS:call,...(kind === "connection" ? {connection:policyReconnect.connection} : {})};
  },kind);
  await panel.locator(mode).selectOption("on");await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => policyReconnect.committed)).toBe(true);
  await panel.locator(mode).selectOption("off");
  await panel.evaluate(host => {
    const parent=host.parentElement,next=document.createElement("extended-openai-management-panel");
    next.hass=host.hass;host.remove();parent.append(next);browserHarness.panel=next;
  });
  await expect(panel.locator(mode)).toBeVisible();await panel.locator(mode).selectOption("custom");
  await page.evaluate(() => policyReconnect.release());
  await expect(panel.locator("#retry-route")).toBeVisible();
  await panel.evaluate((host,kind) => {
    policyReconnect.connected=true;policyReconnect.connection.connected=true;
    const call=host.hass.callWS;
    host.hass={...host.hass,callWS:kind === "function" ? request => call(request) : call};
  },kind);
  const review=panel.locator("dialog[data-eoc-recovered-editor][open]");
  await expect(review).toBeVisible();
  const values=JSON.parse(await review.locator("textarea").inputValue());
  expect(values.recovered.guest_function_policy).toBe("off");expect(values.current.guest_function_policy).toBe("custom");
  await expect(panel.locator("#retry-route")).toHaveCount(0);
  const reads=await page.evaluate(() => policyReconnect.reads);
  await panel.evaluate(async host => {for(let i=0;i<3;i++) host.hass={...host.hass};await new Promise(requestAnimationFrame);});
  expect(await page.evaluate(() => policyReconnect.reads)).toBe(reads);
  expect(await page.evaluate(() => policyReconnect.writes)).toBe(1);
  await expectHarnessClean(page,errors);
});
