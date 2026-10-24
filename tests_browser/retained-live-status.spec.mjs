import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

test("retained Assistant actions duplicate exactly once after repeated bindings", async ({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator(".agent-actions-menu")).toBeVisible();
  await panel.evaluate(async host=>{
    const module=await import("/custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js");
    for(let i=0;i<8;i++)module.bindConfiguration(host);
  });
  await panel.locator(".agent-actions-menu summary").click();
  await panel.locator("#duplicate-agent").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.calls.filter(c=>c.section==="configuration"&&c.action==="duplicate").length)).toBe(1);
  await expectHarnessClean(page,errors);
});

test("pending Duplicate survives a retained menu rebind", async ({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator(".agent-actions-menu")).toBeVisible();
  await panel.evaluate(host=>{
    const original=host._call.bind(host);
    host._call=async(section,action,...args)=>{
      if(section==="configuration"&&action==="duplicate"){
        host._testDuplicateCount=(host._testDuplicateCount||0)+1;
        await new Promise(resolve=>host._testReleaseDuplicate=resolve);
      }
      return original(section,action,...args);
    };
  });
  await panel.locator(".agent-actions-menu summary").click();
  await panel.locator("#duplicate-agent").click();
  await expect.poll(()=>panel.evaluate(host=>host._testDuplicateCount)).toBe(1);
  await panel.evaluate(async host=>{
    const module=await import("/custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js");
    const button=host.shadowRoot.querySelector("#duplicate-agent");
    button.disabled=false;
    module.bindConfiguration(host);
    button.click();
  });
  expect(await panel.evaluate(host=>host._testDuplicateCount)).toBe(1);
  await panel.evaluate(host=>host._testReleaseDuplicate());
  await expect.poll(()=>page.evaluate(()=>browserHarness.calls.filter(c=>c.section==="configuration"&&c.action==="duplicate").length)).toBe(1);
  await expectHarnessClean(page,errors);
});

for(const guest of [true,false])test(`${guest?"Guest Mode":"Quiet Hours"} natural expiry updates status and preserves draft`,async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await panel.evaluate(async(host,guest)=>{
    const original=host._hass.callWS;
    let reads=0;
    host._hass={...host._hass,callWS:async message=>{
      const result=await original(message);
      if(message.section===(guest?"guest_mode":"quiet_hours")&&message.action==="get"){
        reads++;
        if(guest)result.status={state:reads===1?"active":"inactive",currently_active:reads===1,active_from:new Date(Date.now()-1000).toISOString(),active_until:new Date(Date.now()+300).toISOString()};
        else Object.assign(result,{active:reads===1,period_ends_at:new Date(Date.now()+300).toISOString()});
      }
      return result;
    }};
    await host._navigate("capabilities",guest?"guest-mode":"quiet-hours");
    if(guest)host._guestDraft.guest_excluded_entities=["light.keep_draft"];
    else host._quietHoursDraft.max_volume=0.37;
  },guest);
  await expect(panel.locator(guest?"[data-guest-live-status]":"[data-qh-live-badge]")).toContainText("Inactive");
  expect(await panel.evaluate((host,guest)=>guest?host._guestDraft.guest_excluded_entities:host._quietHoursDraft.max_volume,guest)).toEqual(guest?["light.keep_draft"]:0.37);
  await expectHarnessClean(page,errors);
});

test("Broadcast pending history settles without device refresh or draft replacement",async({page})=>{
  const errors=trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator("#broadcast-enabled").check();
  await expect(panel.locator("#broadcast-refresh")).toBeVisible();
  await panel.evaluate(async host=>{
    const original=host._hass.callWS;let reads=0;
    host._hass={...host._hass,callWS:async message=>{
      if(message.type?.endsWith("/broadcast")&&message.action==="snapshot")return {enabled:true,can_manage:true,catalog:{satellites:[],areas:[]},history:[{message:"Queued message",created_at:new Date().toISOString(),deliveries:{"assist_satellite.kitchen":{status:++reads===1?"waiting_idle":"delivered"}}}]};
      return original(message);
    }};
  });
  await panel.locator("#broadcast-refresh").click();
  await expect(panel.locator(".broadcast-history")).toContainText("Waiting for idle");
  await panel.locator("#broadcast-message").fill("Keep this unsent draft");
  await expect(panel.locator(".broadcast-history")).toContainText("Delivered");
  await expect(panel.locator("#broadcast-message")).toHaveValue("Keep this unsent draft");
  await expectHarnessClean(page,errors);
});


test("Broadcast keeps polling pending deliveries beyond recent history", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#broadcast-enabled").check();
  await expect(panel.locator("#broadcast-refresh")).toBeVisible();
  await panel.evaluate(host => {
    const original = host._hass.callWS;
    host._pendingBroadcastReads = 0;
    host._pendingBroadcastReleased = false;
    host._hass = {...host._hass, callWS: async message => {
      if (message.type?.endsWith("/broadcast") && message.action === "snapshot") {
        host._pendingBroadcastReads++;
        const item = {id: "old-pending", message: "Old pending announcement", created_at: new Date().toISOString(),
          deliveries: {"assist_satellite.kitchen": {status: "queued_busy"}}};
        return {enabled: true, can_manage: true, catalog: {satellites: [], areas: []},
          pending: host._pendingBroadcastReleased ? [] : [item],
          history: Array.from({length: 50}, (_, i) => ({id: `new-${i}`, message: `Finished ${i}`,
            created_at: new Date().toISOString(), deliveries: {"assist_satellite.hall": {status: "delivered"}}}))};
      }
      return original(message);
    }};
  });
  await panel.locator("#broadcast-refresh").click();
  await expect(panel.locator(".broadcast-history")).toContainText("Old pending announcement");
  await panel.locator("#broadcast-message").fill("Keep this draft");
  await expect.poll(() => panel.evaluate(host => host._pendingBroadcastReads)).toBeGreaterThanOrEqual(3);
  await panel.evaluate(host => host._pendingBroadcastReleased = true);
  await expect(panel.locator(".broadcast-history")).not.toContainText("Old pending announcement");
  const settled = await panel.evaluate(host => host._pendingBroadcastReads);
  await page.waitForTimeout(1300);
  expect(await panel.evaluate(host => host._pendingBroadcastReads)).toBe(settled);
  await expect(panel.locator("#broadcast-message")).toHaveValue("Keep this draft");
  await expectHarnessClean(page, errors);
});

test("Whole-home Broadcast omits retained specific selections", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#broadcast-enabled").check();
  await panel.evaluate(host => {
    const original = host._hass.callWS;
    host._hass = {...host._hass, callWS: async message => {
      if (message.type?.endsWith("/broadcast") && message.action === "snapshot") return {
        enabled: true, can_manage: true, catalog: {satellites: [{id: "assist_satellite.hall", name: "Hall"}], areas: []},
        history: [], pending: [],
      };
      if (message.type?.endsWith("/broadcast") && message.action === "send") {
        host._sentBroadcast = message;
        return {id: "sent"};
      }
      return original(message);
    }};
  });
  await panel.locator("#broadcast-refresh").click();
  await panel.locator('[data-broadcast-entity="assist_satellite.hall"]').check();
  await panel.locator('input[value="whole"]').check();
  await panel.locator("#broadcast-message").fill("Dinner is ready");
  await panel.locator("#broadcast-send").click();
  await expect.poll(() => panel.evaluate(host => host._sentBroadcast?.whole_home)).toBe(true);
  expect(await panel.evaluate(host => host._sentBroadcast.entity_ids)).toEqual([]);
  await panel.locator('input[value="selected"]').check();
  await expect(panel.locator('[data-broadcast-entity="assist_satellite.hall"]')).toBeChecked();
  await expectHarnessClean(page, errors);
});
