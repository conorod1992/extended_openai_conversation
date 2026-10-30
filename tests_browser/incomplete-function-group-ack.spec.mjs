import {test, expect} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false,true]) test(`incomplete Group reply cannot overwrite the next concurrent edit (${bundled ? "bundle" : "source"})`, async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator(".edit-group").first().click();
  await panel.locator("#group-description").fill("Submitted edit");
  await panel.evaluate(host=>{
    const send=host.hass.callWS.bind(host.hass);
    window.groupSends=[];
    host.hass.callWS=async request=>{
      if(request.section!=="tools"||request.action!=="save_group")return send(request);
      groupSends.push(structuredClone(request));
      const result=await send(request);
      if(groupSends.length===1){
        await send({...request,group:{...request.group,description:"Concurrent edit"},revision:result.revision});
        return {functions:result.functions,function_groups:result.function_groups};
      }
      return result;
    };
    host.shadowRoot.querySelector("#group-dialog").close=()=>{throw Error("modal close failed");};
  });
  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-error")).toContainText("unknown");
  await panel.locator("#group-description").fill("Later user action");
  await panel.locator("#group-save").click();
  await expect.poll(()=>panel.evaluate(host=>host._pendingMutations.size)).toBe(0);
  const actual=await page.evaluate(()=>({requests:groupSends,groups:browserHarness.getState().configuration.config.function_groups}));
  expect(actual.requests, "uncertainty blocks the next replacement before dispatch").toHaveLength(1);
  expect(actual.groups.find(group=>group.id===actual.requests[0].group.id).description).toBe("Concurrent edit");
  await panel.evaluate(async host=>{
    await host._loadConfigDraft({full:true});
    delete host.shadowRoot.querySelector("#group-dialog").close;
  });
  await panel.locator("#group-cancel").click();
  await panel.locator(".edit-group").first().click();
  await expect(panel.locator("#group-description")).toHaveValue("Concurrent edit");
  await panel.locator("#group-description").fill("Edit after authoritative recovery");
  await panel.locator("#group-save").click();
  await expect.poll(()=>page.evaluate(()=>groupSends.length)).toBe(2);
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.function_groups.find(group=>group.id===groupSends[0].group.id).description)).toBe("Edit after authoritative recovery");
});
