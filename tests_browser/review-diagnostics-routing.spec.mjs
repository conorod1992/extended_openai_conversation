import {test,expect} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

for(const bundled of [false,true]) for(const alias of [false,true]) test(`Azure route editor keeps its own underlying model; alias=${alias}, bundle=${bundled}`,async ({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules",bundled?"&bundle=1":""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("button",{name:"Create rule",exact:true}).first()).toBeVisible();
  await panel.evaluate(host=>{
    Object.assign(host._result,{api_provider:"azure",configured_model:"main-deployment",azure_model:"gpt-5",effective_model:"gpt-5"});
    const send=host.hass.callWS.bind(host.hass);window.azureRouteCalls=[];
    host.hass.callWS=async request=>{azureRouteCalls.push(structuredClone(request));return send(request);};
  });
  await panel.getByRole("button",{name:"Create rule",exact:true}).first().click();
  await panel.locator("#rule-name").fill("Azure cheap route");await panel.locator("#rule-phrases").fill("cheap route");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await expect(panel.locator("#rule-azure-model")).toBeVisible();
  await panel.locator("#rule-model").fill(alias?"cheap-deployment":"gpt-4.1-mini");
  if(alias) await panel.locator("#rule-azure-model").fill("gpt-4.1-mini");
  await expect.poll(()=>page.evaluate(()=>azureRouteCalls.filter(call=>call.type.endsWith("/model_catalog")).at(-1)?.model)).toBe("gpt-4.1-mini");
  expect(await page.evaluate(()=>azureRouteCalls.filter(call=>call.type.endsWith("/model_catalog")).at(-1)?.provider)).toBe("azure");
  await panel.locator("#rule-save").click();
  await expect.poll(()=>page.evaluate(()=>azureRouteCalls.find(call=>call.section==="request_rules"&&call.action==="create")?.rule?.action?.model)).toBe(alias?"cheap-deployment":"gpt-4.1-mini");
  const action=await page.evaluate(()=>azureRouteCalls.find(call=>call.section==="request_rules"&&call.action==="create").rule.action);
  expect(action.azure_model).toBe(alias?"gpt-4.1-mini":undefined);
  await expect(panel.locator("#rule-save")).toHaveCount(0);
  await expect(panel.getByText("Azure cheap route",{exact:true}).first()).toBeVisible();
  await expectHarnessClean(page,errors);
});

for(const bundled of [false,true]) for(const excluded of [false,true]) test(`Overview keeps Guest exclusion diagnostics after details and cache; excluded=${excluded}, bundle=${bundled}`,async ({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("overview",bundled?"&bundle=1":""));
  const panel=page.locator("extended-openai-management-panel");await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await panel.evaluate(async (host,excluded)=>{
    const send=host.hass.callWS.bind(host.hass);window.overviewGuestCalls=0;
    host.hass.callWS=async request=>{
      if(request.section!=="overview") return send(request);
      overviewGuestCalls++;const response=await send(request);
      if(request.action==="primary") response.agent={...response.agent,guest_mode:{state:"active_indefinitely",currently_active:true,has_home_assistant_exclusions:excluded}};
      if(request.kind==="guest_mode") response.agent={guest_mode:{state:"active_indefinitely",currently_active:true,has_home_assistant_exclusions:excluded}};
      return response;
    };
    host._sectionCache.clear();await host._loadSectionData(true);
  },excluded);
  await expect.poll(()=>panel.evaluate(host=>host._eocOverviewDetailState?.remaining.size)).toBe(0);
  const warning=panel.getByText("Guest Mode is active without configured Home Assistant exclusions.",{exact:true});
  await expect(warning).toHaveCount(excluded?0:1);
  const requests=await page.evaluate(()=>overviewGuestCalls);
  await panel.evaluate(host=>host._loadSectionData(false));
  await expect(warning).toHaveCount(excluded?0:1);
  expect(await page.evaluate(()=>overviewGuestCalls)).toBe(requests);
  await expectHarnessClean(page,errors);
});
