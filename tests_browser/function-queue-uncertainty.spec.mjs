import {expect,test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";
for (const bundled of [false, true])
for (const fault of ["missing", "lost", "rejected"]) test(`Function queue ${fault} acknowledgement protects subsequent persisted replacement (${bundled ? "bundle" : "source"})`, async ({page})=>{
 await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
 const panel=page.locator("extended-openai-management-panel");
 await expect(panel.locator("#function-add")).toBeVisible();
 const outcome=await panel.evaluate(async(host,fault)=>{
  const call=host.hass.callWS.bind(host.hass);let writes=0;
  host.hass.callWS=async request=>{
   if(request.section!=="tools"||request.action!=="save_group")return call(request);
   writes++;
   if(writes===1&&fault==="rejected")return {valid:false,errors:{group:"rejected"}};
   const saved=await call(request);
   if(writes===1&&fault==="missing"){delete saved.functions;return saved;}
   if(writes===1&&fault==="lost")throw new Error("lost reply");
   return saved;
  };
  const group=structuredClone(host._draft.function_groups[0]);
  const first=host._call("tools","save_group",{group:{...group,enabled:false},original_id:group.id});
  const second=host._call("tools","save_group",{group:{...group,description:"queued assignment"},original_id:group.id});
  const results=await Promise.allSettled([first,second]);
  const blocked=results.map(result=>result.status);
  if(fault!=="rejected"){
   const state=browserHarness.getState();
   if(state.configuration.config.function_groups[0].enabled!==false)throw new Error("Committed disabled state overwritten");
   await host._loadConfigDraft({full:true});
   const recovered=host._draft.function_groups[0];
   await host._call("tools","save_group",{group:{...recovered,description:"after authoritative recovery"},original_id:group.id});
  }
  return {blocked,writes,group:browserHarness.getState().configuration.config.function_groups[0]};
 },fault);
 expect(outcome.blocked).toEqual(fault==="rejected"?["fulfilled","fulfilled"]:["rejected","rejected"]);
 expect(outcome.writes).toBe(2);
 expect(outcome.group.enabled).toBe(fault==="rejected");
});
