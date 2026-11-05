import {expect,test} from "@playwright/test";
import {fixtureUrl,mountFreshAccountPanel,trackPageErrors,expectHarnessClean} from "./browser-helpers.mjs";

for(const bundled of [false,true]) for(const editor of ["configuration","policy"]) for(const reply of ["malformed","lost"]) test(`recovery decision survives a follow-up save: ${editor}, ${reply}, bundle=${bundled}`,async ({page}) => {
  const errors=trackPageErrors(page),route=editor === "configuration" ? "assistant/basics" : "capabilities/guest-mode";
  await page.goto(fixtureUrl(route,bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel"),tokens='[data-config="max_tokens"]',mode='[data-guest-mode="guest_function_policy"]';
  await expect(panel.locator(editor === "configuration" ? tokens : mode)).toBeVisible();
  await panel.evaluate((host,{editor,reply}) => {
    host.hass={...host.hass,user:{id:"recovery-decision-owner",is_admin:true}};
    const send=host.hass.callWS.bind(host.hass);window.recoveryDecision={source:host,writes:[]};
    host.hass.callWS=async request => {
      const mutation=editor === "configuration" ? request.section === "configuration" && request.action === "save" : request.section === "guest_mode" && request.action === "save_policy";
      const result=await send(request);
      if(!mutation) return result;
      recoveryDecision.writes.push(structuredClone(request));
      if(recoveryDecision.writes.length !== 1) return result;
      recoveryDecision.committed=true;await new Promise(resolve => {recoveryDecision.release=resolve;});
      if(reply === "lost") throw {code:3,message:"Connection lost"};
      return {};
    };
  },{editor,reply});
  if(editor === "configuration") {await panel.locator(tokens).fill("373");await panel.locator("#save-config").click();}
  else {
    await panel.locator("#guest-web-search").evaluate(input => {for(let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
    await panel.locator("#guest-web-search").check();await panel.locator(mode).selectOption("on");await panel.locator("#save-page").click();
  }
  await expect.poll(() => page.evaluate(() => recoveryDecision.committed)).toBe(true);
  if(editor === "configuration") await panel.locator(tokens).fill("425");
  else {await panel.locator(mode).selectOption("off");await panel.locator(mode).selectOption("on");await panel.locator(mode).selectOption("off");}
  await mountFreshAccountPanel(panel);
  await expect(panel.locator(editor === "configuration" ? tokens : mode)).toBeVisible();
  if(editor === "configuration") await panel.locator(tokens).fill("477");
  await page.evaluate(() => recoveryDecision.release());
  const review=panel.locator("dialog[data-eoc-recovered-editor][open]");
  if(editor === "configuration") {
    await expect(review).toBeVisible();
    await review.getByRole("button",{name:"Keep draft for later",exact:true}).click();
    await panel.locator('[data-config="__title"]').fill("Destination saved title");await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
    await mountFreshAccountPanel(panel);await expect(review).toBeVisible();
    await expect(panel.locator(tokens)).toHaveValue("477");await expect(panel.locator('[data-config="__title"]')).toHaveValue("Destination saved title");
    expect(JSON.parse(await review.locator("textarea").inputValue()).config.max_tokens).toBe(425);
  } else {
    await expect(panel.locator(mode)).toHaveValue("off");
    await panel.locator("#guest-web-search").evaluate(input => {for(let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
    await panel.locator("#guest-web-search").uncheck();await expect(panel.locator("#save-page")).toBeEnabled();await panel.locator("#save-page").click();
    await expect(panel.locator("#save-page")).toHaveCount(0);
    expect(await page.evaluate(() => browserHarness.getState().guest.config)).toMatchObject({guest_function_policy:"off",guest_web_search:false});
  }
  expect(await page.evaluate(() => recoveryDecision.writes.length)).toBe(2);
  await expectHarnessClean(page,errors);
});
