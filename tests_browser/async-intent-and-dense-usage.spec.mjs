import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";
const management = page => page.locator("extended-openai-management-panel");

for (const failure of [false,true]) test(`Broadcast preserves new drafts and pending send controls (${failure ? "failure" : "success"})`, async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));const panel=management(page);
  await panel.locator("#broadcast-enabled").check();
  await panel.locator("#broadcast-refresh").waitFor();
  await panel.evaluate(async host => {
    const original=host._hass.callWS.bind(host._hass);
    host._hass={...host._hass,callWS:async message=>{
      if(message.type?.endsWith("/broadcast")&&message.action==="snapshot") return {enabled:true,can_manage:true,catalog:{satellites:[{id:"satellite.one",name:"Kitchen",state:"idle"}],areas:[]},history:[]};
      if(message.type?.endsWith("/broadcast")&&message.action==="send") {host._testSendCount=(host._testSendCount||0)+1;return new Promise((resolve,reject)=>{host._releaseSend=resolve;host._rejectSend=reject;});}
      return original(message);
    }};
    host.shadowRoot.querySelector("#broadcast-refresh").click();
  });
  await expect(panel.locator('[data-broadcast-entity="satellite.one"]')).toBeVisible();
  await panel.locator('[data-broadcast-entity="satellite.one"]').check();
  await panel.locator("#broadcast-message").fill("First message");await panel.locator("#broadcast-send").click();
  await expect(panel.locator("#broadcast-send")).toBeDisabled();
  await panel.locator("#broadcast-message").fill("New draft");
  await panel.locator('input[value="whole"]').check();await expect(panel.locator("#broadcast-send")).toBeDisabled();
  await panel.locator("#broadcast-refresh").click();await expect(panel.locator("#broadcast-send")).toBeDisabled();
  await panel.evaluate((host,failure)=>failure?host._rejectSend(new Error("controlled send failure")):host._releaseSend({}),failure);
  await expect(panel.locator("#broadcast-send")).toBeEnabled();await expect(panel.locator("#broadcast-message")).toHaveValue("New draft");
  expect(await panel.evaluate(host=>host._testSendCount)).toBe(1);
  await expectHarnessClean(page,errors);
});

for(const failure of [false,true])test(`Broadcast newest refresh owns roster and selection over stale ${failure?"error":"success"}`,async({page})=>{
  const errors=trackPageErrors(page);await page.goto(fixtureUrl("overview"));const panel=management(page);
  await panel.locator("#broadcast-enabled").check();await panel.locator("#broadcast-refresh").waitFor();
  await panel.evaluate(async host=>{
    const original=host._hass.callWS.bind(host._hass);host._heldSnapshots=[];
    host._hass={...host._hass,callWS:message=>message.type?.endsWith("/broadcast")&&message.action==="snapshot"?new Promise((resolve,reject)=>host._heldSnapshots.push({resolve,reject})):original(message)};
  });
  await panel.locator("#broadcast-refresh").click();await panel.locator("#broadcast-refresh").click();
  await expect.poll(()=>panel.evaluate(host=>host._heldSnapshots.length)).toBe(2);
  await panel.evaluate(host=>host._heldSnapshots[1].resolve({enabled:true,can_manage:true,catalog:{satellites:[{id:"new",name:"New satellite"}],areas:[]},history:[]}));
  await panel.locator('[data-broadcast-entity="new"]').check();
  await panel.evaluate((host,failure)=>failure?host._heldSnapshots[0].reject(new Error("stale error")):host._heldSnapshots[0].resolve({enabled:true,can_manage:true,catalog:{satellites:[],areas:[]},history:[]}),failure);
  await expect(panel.locator('[data-broadcast-entity="new"]')).toBeChecked();
  await expect(panel.locator("#broadcast-card")).not.toContainText("stale error");
  await expectHarnessClean(page,errors);
});

for(const width of [320,390,1280])test(`Dense 90-day Usage geometry and accessible scrolling at ${width}px`,async({page})=>{
  const errors=trackPageErrors(page);await page.setViewportSize({width,height:900});
  await page.clock.install({time:new Date("2026-09-29T12:00:00Z")});
  await page.goto(fixtureUrl("overview"));const panel=management(page);await panel.locator(".dashboard-grid").waitFor();
  await panel.evaluate(async host=>{
    const original=host._hass.callWS.bind(host._hass);
    host._hass={...host._hass,callWS:async message=>{
      if(message.section==="usage"&&message.action==="daily")return {days:Array.from({length:90},(_,index)=>({date:new Date(Date.UTC(2026,6,2+index)).toISOString().slice(0,10),total_tokens:index===0?100:index===1?200:index===2?1:400,input_tokens:400,cached_input_tokens:index===0?25:index===1?100:0}))};
      return original(message);
    }};await host._navigate("usage-maintenance","usage");
  });
  await panel.locator("#usage-window").selectOption("90");await expect(panel.locator(".chart-column")).toHaveCount(90);
  const geometry=await panel.evaluate(host=>{
    const chart=host.shadowRoot.querySelector(".chart"),bars=[...chart.querySelectorAll(".chart-column")];
    return {pageWidth:document.documentElement.scrollWidth,viewport:innerWidth,chartWidth:chart.clientWidth,contentWidth:chart.scrollWidth,
      bars:bars.slice(0,4).map(bar=>({height:bar.getBoundingClientRect().height,cached:bar.querySelector(".cached").getBoundingClientRect().height})),label:bars.at(-1).getAttribute("aria-label")};
  });
  expect(geometry.pageWidth).toBeLessThanOrEqual(geometry.viewport+1);
  expect(geometry.bars[1].height/geometry.bars[0].height).toBeCloseTo(2,1);
  expect(geometry.bars[0].cached/geometry.bars[0].height).toBeCloseTo(.25,1);
  expect(geometry.bars[1].cached/geometry.bars[1].height).toBeCloseTo(.5,1);
  expect(geometry.bars[2].height/geometry.bars[3].height).toBeCloseTo(.02,2);
  if(width<400)expect(geometry.contentWidth).toBeGreaterThan(geometry.chartWidth);
  await panel.locator(".chart-column").last().focus();
  await expect(panel.locator(".chart-column").last()).toBeFocused();
  expect(geometry.label).toContain("400");
  await expectHarnessClean(page,errors);
});

test("Request Debug ignores a pre-disable read and still loads with blocked storage",async({page})=>{
  const errors=trackPageErrors(page);await page.goto(fixtureUrl("usage-maintenance/request-debug"));
  const debug=page.locator("extended-openai-debug-panel");await expect(debug.locator("#enabled")).toBeEnabled();
  await debug.locator("#enabled").check();await expect(debug.locator("#enabled")).toBeChecked();
  await debug.evaluate(host=>{
    const original=host._hass.callWS.bind(host._hass);let first=true;
    host._hass={...host._hass,callWS:message=>{
      if(first&&message.action==="runs"){first=false;return new Promise(resolve=>host._releaseOldRead=resolve);}
      return original(message);
    }};void host._loadRuns();
  });
  await debug.locator("#enabled").uncheck();await expect(debug.locator("#enabled")).not.toBeChecked();
  await debug.evaluate(host=>host._releaseOldRead({enabled:true,limit:10,count:1,runs:[{debug_id:"stale"}]}));
  await expect(debug.locator("#enabled")).not.toBeChecked();await expect(debug.locator('[data-view="stale"]')).toHaveCount(0);
  await debug.evaluate(async host=>{
    Object.defineProperty(window,"localStorage",{configurable:true,get(){throw new Error("storage blocked");}});
    await host._loadAgents();
  });
  await expect(debug.locator("#enabled")).toBeEnabled();await expect(debug.locator(".error-box")).toHaveCount(0);
  await expectHarnessClean(page,errors);
});
