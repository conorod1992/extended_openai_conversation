import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

async function replacePanel(panel, remember=false) {
  await panel.evaluate((host,remember) => {
    if(remember) competingGuest.destination=host;
    const next=document.createElement("extended-openai-management-panel"),parent=host.parentElement;
    next.hass=host.hass;host.remove();parent.append(next);browserHarness.panel=next;
  },remember);
  await expect(panel.locator("#guest-start")).toBeAttached();
}
async function setEnd(panel,value) {
  await panel.locator("#guest-indefinite").uncheck();
  await panel.locator("#guest-end").fill(value);
  await panel.locator("#guest-indefinite").check();
}
for(const bundled of [false,true]) for(const editor of ["policy","interval"]) for(const destination of ["disjoint","conflicting"]) for(const reply of ["success","malformed","lost"]) for(const visits of [1,2]) test(`competing Guest ${editor}: ${destination}, ${reply}, visits=${visits}, bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode",bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  const mode='[data-guest-mode="guest_function_policy"]';
  await expect(panel.locator(mode)).toBeVisible();
  await panel.evaluate(async (host,{editor,reply}) => {
    host.hass={...host.hass,user:{id:"competing-guest-owner",is_admin:true},config:{...host.hass.config,time_zone:"UTC"}};
    const send=host.hass.callWS.bind(host.hass),saved=browserHarness.getState().guest;
    await send({type:"extended_openai_conversation_responses/management",section:"guest_mode",action:"save_policy",entry_id:host._selectedAgent().entry_id,subentry_id:host._agentId,revision:saved.revision,config:{...saved.config,guest_function_policy:"off"}});
    await host._loadSection(true);
    window.competingGuest={source:host,writes:[],status:{active_from:null,active_until:null,indefinite:editor === "interval" && reply !== "success"}};
    host.hass.callWS=async request => {
      if(request.section !== "guest_mode") return send(request);
      const action=editor === "policy" ? "save_policy" : "update";
      if(request.action !== action) {
        const result=await send(request);
        return request.action === "get" ? {...result,status:structuredClone(competingGuest.status)} : result;
      }
      competingGuest.writes.push(structuredClone(request));
      if(competingGuest.writes.length !== 1) return send(request);
      competingGuest.entered=true;
      await new Promise(resolve => {competingGuest.commit=resolve;});
      const result=await send(request);
      if(editor === "interval") {
        competingGuest.status={active_from:request.local_times?.active_from ? `${request.active_from}:00Z` : request.active_from,active_until:null,indefinite:true};
        result.status=structuredClone(competingGuest.status);
      }
      competingGuest.committed=true;
      await new Promise(resolve => {competingGuest.reply=resolve;});
      if(reply === "lost") throw {code:3,message:"Connection lost"};
      return reply === "malformed" ? {} : result;
    };
    if(editor === "interval" && reply !== "success") await host._loadSection(true);
  },{editor,reply});
  if(editor === "policy") {
    await panel.locator("#guest-web-search").evaluate(input => {for(let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
    await panel.locator("#guest-web-search").check();
    await panel.locator(mode).selectOption("on");await panel.locator("#save-page").click();
  } else {
    await panel.locator("#guest-start").fill("2027-06-04T12:00");
    if(reply === "success") await setEnd(panel,"2027-06-04T13:00");
    else await panel.locator("#guest-indefinite").check();
    await panel.locator("#guest-update").click();
  }
  await expect.poll(() => page.evaluate(() => competingGuest.entered)).toBe(true);
  if(editor === "policy") await panel.locator(mode).selectOption("off");
  else if(reply === "success") await setEnd(panel,"");
  else await panel.locator("#guest-start").fill("2027-06-04T13:00");
  await page.evaluate(() => competingGuest.commit());
  await expect.poll(() => page.evaluate(() => competingGuest.committed)).toBe(true);
  if(editor === "policy") {
    await panel.locator(mode).selectOption("on");await panel.locator(mode).selectOption(reply === "success" ? "custom" : "off");
  } else if(reply === "success") await setEnd(panel,"2027-06-04T17:00");
  else {
    await panel.locator("#guest-start").fill("2027-06-04T12:00");
    await panel.locator("#guest-start").fill("");
  }
  const source=await panel.evaluate((host,editor) => structuredClone(editor === "policy" ? host._guestDraft : host._guestIntervalDraft.values),editor);
  await replacePanel(panel,true);
  if(editor === "policy") {
    if(destination === "disjoint") {
      await panel.locator("#guest-web-search").evaluate(input => {for(let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
      await panel.locator("#guest-web-search").uncheck();
    }
    else await panel.locator(mode).selectOption(reply === "success" ? "off" : "custom");
  } else if((destination === "disjoint") !== (reply === "success")) await setEnd(panel,"2027-06-04T16:00");
  else await panel.locator("#guest-start").fill("2027-06-04T16:00");
  const authored=await panel.evaluate((host,editor) => structuredClone(editor === "policy" ? host._guestDraft : host._guestIntervalDraft.values),editor);
  if(visits === 2) await replacePanel(panel,true);
  await page.evaluate(() => competingGuest.reply());
  const review=panel.locator("dialog[data-eoc-recovered-editor][open]");
  await expect(review).toBeVisible();
  const copies=JSON.parse(await review.locator("textarea").inputValue());
  expect([copies.recovered,copies.current]).toEqual(expect.arrayContaining([source,authored]));
  expect(await page.evaluate(() => competingGuest.writes.length)).toBe(1);
  await review.getByRole("button",{name:"Keep draft for later",exact:true}).click();
  await expect(review).toHaveCount(0);
  await panel.locator('.subsection-nav button[data-subsection="guest-mode"]').click();
  await expect(review).toBeVisible();
  const current=JSON.parse(await review.locator("textarea").inputValue()).current;
  await review.getByRole("button",{name:"Discard recovered edits",exact:true}).click();
  await expect(review).toHaveCount(0);
  expect(await panel.evaluate((host,editor) => editor === "policy" ? host._guestDraft : host._guestIntervalDraft.values,editor)).toEqual(current);
  expect(await page.evaluate(() => competingGuest.writes.length)).toBe(1);
  await expectHarnessClean(page,errors);
});
