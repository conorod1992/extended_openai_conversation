import {expect, test} from "@playwright/test";
import {fixtureUrl, acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) test(`Discard on assistant switch retains committed cleanup after a failed recovery read; bundle=${bundled}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
  const source = page.locator("extended-openai-management-panel").first();
  await expect(source.locator('[data-config="max_tokens"]')).toHaveValue("1200");
  await source.evaluate(host => {host.id="discard-source"; host.hass={...host.hass, user:{id:"discard-admin", is_admin:true}};});
  await expect.poll(() => source.evaluate(host => !host._busy && !host._eocCatalogBootstrap?.pending)).toBe(true);
  await source.evaluate(host => {
    const target=document.createElement("extended-openai-management-panel");
    target.id="discard-target"; target.hass=host.hass;
    document.body.append(target); browserHarness.panel=target;
    const send=host.hass.callWS.bind(host.hass);
    window.discardRecovery={committed:false, failReads:true};
    host.hass.callWS=async request => {
      const state=window.discardRecovery;
      if (request.section==="configuration" && request.action==="get" && request.subentry_id==="agent-1" && state.committed && state.failReads) throw {code:"offline", message:"Refresh unavailable"};
      if (request.section==="configuration" && request.action==="import_preview") return {title:"Imported", summary:{model:"gpt-5-mini", tools:0, function_groups:0, speech_rules:0}};
      if (request.section==="configuration" && request.action==="import") {
        await send({...request, action:"save", title:"Imported", config:{usage_request_retention_days:0, max_tokens:1600}});
        state.committed=true;
        throw {code:"eoai_mutation_committed", message:"Retention deletion failed"};
      }
      return send(request);
    };
  });
  const target=page.locator("#discard-target");
  await expect(target.locator('[data-config="max_tokens"]')).toHaveValue("1200");
  await target.locator('[data-config="max_tokens"]').fill("888");
  await source.locator(".agent-actions-menu > summary").click(); await source.locator("#import-agent").click();
  await source.locator('input[name="import-mode"][value="current"]').check();
  await source.locator("#import-document").fill("reviewed imported configuration"); await source.locator("#import-preview").click();
  await source.locator("#import-apply").click(); await acceptConfirmation(source);
  await expect.poll(() => source.evaluate(host => host._eocImportPending)).toBe(false);
  await source.locator("#import-cancel").click();
  await source.locator('[data-config="max_tokens"]').fill("999");
  await target.evaluate(async host => {document.querySelector("#discard-source").remove(); await host._loadSection(true);});
  await expect(target.getByText("Unsaved configuration is retained. Refresh this section to retry loading the complete settings.", {exact:true})).toBeVisible();
  await target.locator("#agent").selectOption("scale-agent-1"); await acceptConfirmation(target);
  await expect.poll(() => target.evaluate(host => host._agentId==="scale-agent-1" && !host._busy)).toBe(true);
  expect(await target.evaluate(host => host._eocPendingRetention?.agentId)).toBeUndefined();
  await page.evaluate(() => {discardRecovery.failReads=false;});
  await target.locator("#agent").selectOption("agent-1");
  await expect.poll(() => target.evaluate(host => host._eocPendingRetention?.fields.length)).toBe(2);
  await expect(target.locator('[data-config="max_tokens"]')).toHaveValue("1600");
  await expect(target.locator("dialog[data-eoc-recovered-editor][open]")).toHaveCount(0);
  await target.locator("#save-config").click();
  await expect.poll(() => target.evaluate(host => host._configDirty)).toBe(false);
  const writes=await page.evaluate(() => browserHarness.calls.filter(call => call.section==="configuration" && call.action==="save"));
  expect(writes.at(-1).config).toEqual({usage_request_retention_days:0, usage_run_retention_days:30});
  await expectHarnessClean(page, errors);
});
