import {test,expect} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";
test("incomplete Group acknowledgement recovers collections before the next replacement",async({page})=>{
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator("#rule-groups-manage").click();
  await panel.evaluate(host=>{
    const send=host.hass.callWS.bind(host.hass);let first=true;
    host.hass.callWS=async request=>{const result=await send(request);if(first&&request.section==="request_rules"&&request.action==="groups"){first=false;return {revision:result.revision};}return result;};
  });
  await panel.locator("#rule-new-group-name").fill("First persisted group");
  await panel.locator("#rule-group-add").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().requestRules.groups.length)).toBe(1);
  await expect.poll(()=>panel.evaluate(host=>host._result?.groups?.length)).toBe(1);
  if(await panel.locator(".rule-groups").evaluate(dialog=>dialog.open)) await panel.locator("#rule-groups-done").click();
  await panel.locator("#rule-groups-manage").click();
  await panel.locator("#rule-new-group-name").fill("Second persisted group");
  await panel.locator("#rule-group-add").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().requestRules.groups.map(group=>group.name))).toEqual(["First persisted group","Second persisted group"]);
});
