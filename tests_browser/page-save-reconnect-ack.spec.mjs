import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const policies = [
  {kind:"quiet",view:"capabilities/quiet-hours",section:"quiet_hours",get:"get",save:"update",primary:"#qh-wake",value:"unchanged",later:"on",original:"off",secondary:"#qh-start",second:"23:00"},
  {kind:"guest",view:"capabilities/guest-mode",section:"guest_mode",get:"get",save:"save_policy",primary:'[data-guest-mode="guest_function_policy"]',value:"custom",later:"on",original:"off",secondary:"#guest-web-search",second:true},
  {kind:"rules",view:"capabilities/request-rules",section:"request_rules",get:"list",save:"settings",primary:"#rules-default-fuzzy",value:true,later:false,original:false,secondary:"#rules-default-word-forms",second:false},
];

async function ready(panel, policy) {
  await expect(panel.locator(policy.primary)).toHaveCount(1);
  await expect.poll(() => panel.evaluate(host => Boolean(host._busy || host._error))).toBe(false);
  if(policy.kind === "rules") {
    const details=panel.locator(".rule-settings details").first();
    if(!await details.evaluate(node => node.open)) await details.locator("summary").first().click();
  }
}

async function edit(panel, selector, value) {
  const control=panel.locator(selector);
  const type=await control.evaluate(node => node.type);
  if(type === "checkbox") await control.setChecked(value);
  else if(await control.evaluate(node => node.tagName === "SELECT")) await control.selectOption(String(value));
  else await control.fill(String(value));
}

async function value(panel, selector) {
  return panel.locator(selector).evaluate(node => node.type === "checkbox" ? node.checked : node.value);
}

async function replace(panel) {
  await panel.evaluate(host => {
    const parent=host.parentElement, hass=host.hass;host.remove();
    const next=document.createElement("extended-openai-management-panel");parent.append(next);next.hass=hass;
  });
}

async function persisted(page, kind) {
  return page.evaluate(kind => {
    const state=browserHarness.getState();
    return kind === "quiet" ? state.quiet.config.wake_sound
      : kind === "guest" ? state.guest.config.guest_function_policy || "off" : state.requestRules.defaults.fuzzy;
  },kind);
}

for(const bundled of [false,true]) for(const policy of policies)
  for(const scenario of ["connected","clean","write-failure","cold-error","refresh-error","source-edit","destination-edit","destination-error","replace-during-refresh"]) {
    test(`${policy.kind} page ACK: ${scenario} (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors=trackPageErrors(page);
      await page.goto(fixtureUrl(policy.view,bundled ? "&bundle=1" : ""));
      const panel=page.locator("extended-openai-management-panel");
      await ready(panel,policy);
      await panel.evaluate((host,{policy,scenario}) => {
        host.hass={...host.hass,user:{id:"admin",is_admin:true}};
        const original=host.hass.callWS.bind(host.hass);
        const state={source:host,policy,scenario,writes:[],reads:0,writeDone:false};window.pageAck=state;
        host.hass.callWS=async request => {
          if(request.section === policy.section && request.action === policy.save) {
            state.writes.push(structuredClone(request));
            if(state.writes.length === 1) {
              state.writeEntered=true;await new Promise(resolve => {state.releaseWrite=resolve;});
              if(scenario === "write-failure") {state.writeDone=true;throw new Error("Controlled policy write failure");}
            }
          }
          const result=await original(request);
          if(request.section === policy.section && request.action === policy.save) state.writeDone=true;
          if(request.section === policy.section && request.action === policy.get) {
            state.reads++;
            if(state.reads === 1 && scenario !== "connected") {
              state.coldEntered=true;await new Promise(resolve => {state.releaseCold=resolve;});
              if(scenario === "cold-error") throw new Error("Controlled old policy read failure");
            } else if(state.reads === 2 && ["destination-edit","destination-error","replace-during-refresh"].includes(scenario)) {
              state.refreshEntered=true;await new Promise(resolve => {state.releaseRefresh=resolve;});
              if(scenario === "destination-error") throw new Error("Controlled policy certification failure after editing");
            } else if(state.reads === 2 && scenario === "refresh-error") {
              state.refreshFailed=true;throw new Error("Controlled policy certification failure");
            }
          }
          return result;
        };
      },{policy,scenario});
      await edit(panel,policy.primary,policy.value);
      await panel.locator("#save-page").click();
      await expect.poll(() => page.evaluate(() => pageAck.writeEntered)).toBe(true);
      if(scenario === "source-edit") await edit(panel,policy.primary,policy.later);
      if(scenario !== "connected") {
        await replace(panel);
        await expect.poll(() => page.evaluate(() => pageAck.coldEntered)).toBe(true);
      }
      await page.evaluate(() => pageAck.releaseWrite());
      await expect.poll(() => page.evaluate(() => pageAck.writeDone && !pageAck.source._unsavedState.scopes.get(pageAck.policy.view).pending)).toBe(true);
      if(scenario !== "connected") await page.evaluate(() => pageAck.releaseCold());
      if(scenario === "cold-error" || scenario === "refresh-error") {
        await expect(panel.locator("#retry-route")).toBeVisible();
        expect(await page.evaluate(() => Boolean(pageAck.source._unsavedState.scopes.get(pageAck.policy.view).acknowledgedSave?.owns()))).toBe(true);
        await panel.locator("#retry-route").click();
      }
      if(scenario.startsWith("destination-") || scenario === "replace-during-refresh") {
        await expect.poll(() => page.evaluate(() => pageAck.refreshEntered)).toBe(true);
        await ready(panel,policy);
        if(scenario.startsWith("destination-")) {
          await edit(panel,policy.secondary,policy.second);
          await expect(panel.locator("#save-page")).toBeDisabled();
          await expect(panel.locator("#save-page")).toHaveText("Refreshing saved policy…");
          const blocked=await panel.evaluate(async (host,view) => {
            try {await host._unsavedState.scopes.get(view).save();return "write admitted";}
            catch(error) {return error.message;}
          },policy.view);
          expect(blocked).toContain("still being refreshed");
          expect(await page.evaluate(() => pageAck.writes.length)).toBe(1);
        } else await replace(panel);
        await page.evaluate(() => pageAck.releaseRefresh());
        if(scenario === "destination-error") {
          await expect(panel.locator("#retry-route")).toBeVisible();
          const blocked=await panel.evaluate(async (host,view) => {
            try {await host._unsavedState.scopes.get(view).save();return "write admitted";}
            catch(error) {return error.message;}
          },policy.view);
          expect(blocked).toContain("still being refreshed");
          expect(await page.evaluate(() => pageAck.writes.length)).toBe(1);
          await panel.locator("#retry-route").click();
        }
      }
      await ready(panel,policy);
      if(scenario !== "connected" && scenario !== "write-failure") {
        await expect.poll(() => page.evaluate(() => Boolean(pageAck.source._unsavedState.scopes.get(pageAck.policy.view).acknowledgedSave))).toBe(false);
      }
      expect(await page.evaluate(() => pageAck.writes.length)).toBe(1,"reads and recovery must not replay the write");
      expect(await persisted(page,policy.kind)).toBe(scenario === "write-failure" ? policy.original : policy.value);
      if(scenario.startsWith("destination-")) {
        expect(await value(panel,policy.secondary)).toBe(policy.second);
        if(policy.kind === "quiet") {
          await panel.locator("#save-page").click();
          await expect(panel.getByText(/saved Quiet Hours policy changed/)).toBeVisible();
          expect(await page.evaluate(() => pageAck.writes.length)).toBe(1);
          expect(await value(panel,policy.secondary)).toBe(policy.second);
        }
        await panel.locator("#discard-page").click();
        await ready(panel,policy);
        expect(await value(panel,policy.primary)).toBe(policy.value,"explicit discard adopts the freshly observed saved policy");
        await edit(panel,policy.secondary,policy.second);
        await panel.locator("#save-page").click();
        await expect.poll(() => page.evaluate(() => pageAck.writes.length)).toBe(2);
        await expect(panel.locator("#save-page")).toHaveCount(0);
        expect(await persisted(page,policy.kind)).toBe(policy.value,"later edits cannot undo the completed source write");
      } else {
        const expected=scenario === "source-edit" ? policy.later : policy.value;
        expect(await value(panel,policy.primary)).toBe(expected);
        const dirty=scenario === "source-edit" || scenario === "write-failure";
        await expect(panel.locator("#save-page")).toHaveCount(dirty ? 1 : 0);
        if(dirty) {
          await panel.locator("#save-page").click();
          await expect.poll(() => page.evaluate(() => pageAck.writes.length)).toBe(2);
          await expect(panel.locator("#save-page")).toHaveCount(0);
          expect(await persisted(page,policy.kind)).toBe(expected);
        }
      }
      if(scenario === "connected") expect(await page.evaluate(() => pageAck.reads)).toBe(0,"ordinary connected saves retain targeted presentation");
      await expectHarnessClean(page,errors);
    });
  }
