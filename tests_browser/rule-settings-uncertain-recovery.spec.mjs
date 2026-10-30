import {test, expect} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";
for (const bundle of [false,true]) for (const fault of ["lost", "unknown_error", "missing revision", "missing defaults", "missing wording groups"]) test(`settings recover after ${fault} (${bundle ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#rules-default-word-forms")).toBeAttached();
  await panel.locator(".rule-settings details").evaluateAll(nodes => nodes.forEach(node => {node.open=true;}));
  await panel.evaluate(async (host, fault) => {
    window.settingsWrites=0;
    const send=host.hass.callWS.bind(host.hass);
    let hold=true;
    host.hass.callWS=async message => {
      if (message.section==="request_rules" && message.action==="settings") settingsWrites++;
      const result=await send(message);
      if (hold && message.section==="request_rules" && message.action==="list") {
        hold=false; await new Promise(resolve => {window.releaseOldRuleRead=resolve;});
      }
      if (message.section==="request_rules" && message.action==="settings") {
        if (settingsWrites===1) {
          if (fault==="unknown_error") throw {code:"unknown_error",message:"Unknown error"};
          if (fault==="lost") throw {error:{code:3,message:"Reply lost"}};
          if (fault==="missing defaults") {delete result.defaults; return result;}
          if (fault==="missing wording groups") {delete result.wording_groups; return result;}
          const {revision,...incomplete}=result; return incomplete;
        }
      }
      return result;
    };
    window.oldRuleRead=host._call("request_rules","list");
  }, fault);
  await expect.poll(()=>page.evaluate(()=>typeof window.releaseOldRuleRead)).toBe("function");
  await panel.locator("#rules-default-word-forms").uncheck();
  await panel.locator("#save-page").click();
  await expect(panel.locator("#save-page")).toBeEnabled();
  expect(await page.evaluate(()=>browserHarness.getState().requestRules.defaults.word_forms)).toBe(false);
  await page.evaluate(async()=>{releaseOldRuleRead(); await oldRuleRead;});
  // A read captured before the commit must not authorize another write.
  await panel.locator("#save-page").click();
  await expect(panel.locator("#save-page")).toBeEnabled();
  expect(await page.evaluate(()=>settingsWrites)).toBe(1);
  // A newer but incomplete list must not certify recovery either.
  await panel.evaluate(async host=>{
    const send=host.hass.callWS.bind(host.hass);
    host.hass.callWS=async message=>{
      const result=await send(message);
      return message.section==="request_rules" && message.action==="list" ? {revision:result.revision} : result;
    };
    try {await host._call("request_rules","list");} finally {host.hass.callWS=send;}
  });
  await panel.locator("#save-page").click();
  await expect(panel.locator("#save-page")).toBeEnabled();
  expect(await page.evaluate(()=>settingsWrites)).toBe(1);
  // Ordinary section refresh must recover, without a collection/editor detour.
  await panel.evaluate(async host=>{host._sectionCache.clear(); await host._loadSection(true);});
  await panel.locator(".rule-settings details").evaluateAll(nodes=>nodes.forEach(node=>{node.open=true;}));
  // A refresh must not silently rebase dirty settings over concurrent changes.
  // Explicitly discard the retained draft against the freshly read baseline.
  await panel.locator("#discard-page").click();
  await panel.locator("#rules-default-wording").uncheck();
  await panel.locator("#save-page").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().requestRules.defaults.wording_alternatives)).toBe(false);
  expect(await page.evaluate(()=>settingsWrites)).toBe(2);
  expect(await page.evaluate(()=>browserHarness.getState().requestRules.defaults.word_forms)).toBe(false);
  await expectHarnessClean(page,errors);
});
