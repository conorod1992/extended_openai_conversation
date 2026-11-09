import {test, expect} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false,true]) for (const view of ["request-rules","guest-mode"]) {
  const route=`capabilities/${view}`, suffix=bundled?"&bundle=1":"";
  test(`cold native selectors ${view} bundle=${bundled}`,async ({page}) => {
    const errors=trackPageErrors(page);
    const savedPolicy={guest_excluded_labels:["private"],guest_excluded_areas:["office"],guest_excluded_domains:["lock"],guest_excluded_entities:["light.office"],guest_knowledge_policy:"custom",guest_knowledge_source_ids:["known-source"],guest_function_policy:"custom",guest_allowed_function_names:["baseline_tool"],guest_allowed_group_ids:["baseline-group"]};
    if(view==="guest-mode") {
      await page.goto(fixtureUrl("guide"));
      await expect.poll(()=>page.evaluate(()=>Boolean(window.browserHarness?.panel?._data))).toBe(true);
      await page.evaluate(config=>browserHarness.hass.callWS({type:"extended_openai_conversation_responses/management",section:"guest_mode",action:"save_policy",config,revision:"guest-1"}),savedPolicy);
    }
    // This navigation creates a new document; no HA editor has registered a
    // selector in the document which now hosts the actual management entry.
    await page.goto(fixtureUrl(route,`${suffix}&selector_load=hold`));
    const panel=page.locator("extended-openai-management-panel");
    await expect.poll(()=>page.evaluate(()=>selectorFixture.loads)).toBe(1);
    expect(await page.evaluate(()=>({initial:selectorFixture.initiallyUndefined,registered:Boolean(customElements.get("ha-selector"))}))).toEqual({initial:true,registered:false});
    await expect(panel.locator("main .loading")).toBeVisible();
    expect(await panel.locator("ha-selector").count()).toBe(0);
    await page.evaluate(()=>selectorFixture.release());
    if(view==="request-rules") {
      await panel.locator("#rule-add").click();
      await panel.locator("#rule-add-conditions").click();
      await expect(panel.locator("#rule-conditions-body")).toBeVisible();
    } else {
      await expect(panel.locator(".guest-intro")).toBeVisible();
      const modes=panel.locator("select[data-guest-mode]");
      for (const mode of await modes.all()) if(await mode.locator('option[value="custom"]').count()) await mode.selectOption("custom");
    }
    const selectors=panel.locator(view==="request-rules"?"#rule-action-sequence-host ha-selector,#rule-condition-host ha-selector":"ha-selector[data-guest-key]");
    const states=await selectors.evaluateAll(nodes=>nodes.map(node=>({upgraded:Boolean(node.shadowRoot),hass:Boolean(node.hass),shadowed:["selector","value","hass"].some(key=>Object.hasOwn(node,key))})));
    expect(states.length).toBeGreaterThanOrEqual(view==="request-rules"?2:7);
    for(const state of states) expect(state).toEqual({upgraded:true,hass:true,shadowed:false});
    if(view==="guest-mode") {
      const values=await selectors.evaluateAll(nodes=>Object.fromEntries(nodes.map(node=>[node.dataset.guestKey,node.value])));
      for(const [key,value] of Object.entries(values)) expect(value).toEqual(savedPolicy[key]);
    }
    await expectHarnessClean(page,errors);
  });
  test(`native selector failure is visible and retryable ${view} bundle=${bundled}`,async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl(route,`${suffix}&selector_load=fail`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator(".route-error-state")).toContainText("Controlled native selector load failure");
    expect(await panel.locator("ha-selector").count()).toBe(0);
    await page.evaluate(()=>{selectorFixture.fail=false;});
    await panel.locator("#retry-route").click();
    await expect(panel.locator(view==="request-rules"?"#rule-add":".guest-intro")).toBeVisible();
    expect(await page.evaluate(()=>selectorFixture.loads)).toBe(2);
    await expectHarnessClean(page,errors);
  });
}
