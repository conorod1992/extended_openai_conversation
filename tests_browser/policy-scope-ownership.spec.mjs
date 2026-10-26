import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const policies = [
  {view:"capabilities/guest-mode", section:"guest_mode", action:"save_policy", field:"_guestDraft", selector:"#guest-web-search"},
  {view:"capabilities/quiet-hours", section:"quiet_hours", action:"update", field:"_quietHoursDraft", selector:"#qh-enabled"},
  {view:"capabilities/request-rules", section:"request_rules", action:"settings", field:"_rulesSettingsDraft", selector:"#rules-default-fuzzy"},
];

for(const bundled of [false,true]) for(const policy of policies) for(const owner of ["user","entry","session"]) {
  test(`${policy.section}: late ACK after ${owner} retirement cannot publish into the new scope (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl(policy.view,bundled ? "&bundle=1" : ""));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator(policy.selector)).toHaveCount(1);
    await panel.evaluate(async (host,policy) => {
      const call=host.hass.callWS.bind(host.hass);
      host.hass={...host.hass,user:{id:"original-owner",is_admin:true}};
      await host._loadSection(true);
      host.hass={...host.hass,user:{id:"original-owner",is_admin:true},callWS:async request => {
        if(request.section !== policy.section || request.action !== policy.action) return call(request);
        window.sourcePolicyScope=host._unsavedState.scopes.get(policy.view);
        return new Promise(resolve => {window.finishPolicySave=async () => resolve(await call(request));});
      }};
    },policy);
    if(policy.section === "request_rules") {
      const details=panel.locator(".rule-settings details").first();
      if(!await details.evaluate(node => node.open)) await details.locator("summary").click();
    }
    const initial=await panel.locator(policy.selector).isChecked();
    await panel.locator(policy.selector).setChecked(!initial);
    await panel.locator("#save-page").click();
    await expect.poll(() => page.evaluate(() => typeof window.finishPolicySave)).toBe("function");
    await panel.evaluate(async (host,{policy,owner}) => {
      if(owner === "user") host.hass={...host.hass,user:{id:"new-owner",is_admin:true}};
      if(owner === "entry") host._selectedAgent().entry_id="new-provider-entry";
      if(owner === "session") host._clearConfigDraft();
      await host._loadSection(true);
      window.targetPolicyScope=host._unsavedState.scopes.get(policy.view);
    },{policy,owner});
    await expect(panel.locator(policy.selector)).toHaveCount(1);
    if(policy.section === "request_rules") {
      const details=panel.locator(".rule-settings details").first();
      if(!await details.evaluate(node => node.open)) await details.locator("summary").click();
    }
    await panel.locator(policy.selector).setChecked(!initial);
    await panel.evaluate((host,policy) => {
      window.targetPolicyDraft=host[policy.field];
      window.targetPolicyValues=structuredClone(host[policy.field]);
      window.targetPolicyRevision=targetPolicyScope.revision;
    },policy);
    await page.evaluate(() => finishPolicySave());
    await expect.poll(() => page.evaluate(() => sourcePolicyScope.pending)).toBe(false);
    const state=await panel.evaluate((host,policy) => ({
      differentScope:targetPolicyScope !== sourcePolicyScope,
      sameDraft:host[policy.field] === targetPolicyDraft,
      values:host[policy.field],expected:targetPolicyValues,
      revision:targetPolicyScope.revision,expectedRevision:targetPolicyRevision,
      writes:browserHarness.calls.filter(call => call.section === policy.section && call.action === policy.action).length,
    }),policy);
    expect(state.differentScope).toBe(true);expect(state.sameDraft).toBe(true);
    expect(state.values).toEqual(state.expected);expect(state.revision).toBe(state.expectedRevision);
    expect(state.writes).toBe(1);
    await expect(panel.locator(policy.selector)).toBeChecked({checked:!initial});
    await expectHarnessClean(page,errors);
  });
}
