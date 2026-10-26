import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function prepare(page, bundled, holdWrites = false) {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("button", {name:"Create rule", exact:true}).first()).toBeVisible();
  await panel.evaluate((host, holdWrites) => {
    const original = host.hass.callWS.bind(host.hass);
    const probe = window.ruleAsyncOwner = {lookups:[], writes:[], toasts:[]};
    host._toast = (...args) => probe.toasts.push(args);
    host.hass = {...host.hass, user:{id:"rule-owner-A", is_admin:true}, callWS:request => {
      const lookup = request.type?.endsWith("/model_catalog") && request.action === "lookup";
      const write = holdWrites && request.section === "request_rules" && ["create","update"].includes(request.action);
      if (!lookup && !write) return original(request);
      return new Promise((resolve, reject) => {
        const record = {request:structuredClone(request), finish:async (failed, efforts = ["high"]) => {
          if (failed) reject(Error("Obsolete Rule operation failed"));
          else {
            const result = await original(request);
            resolve(lookup ? {...result, reasoning_effort_options:efforts} : result);
          }
          record.released = true;
        }};
        (lookup ? probe.lookups : probe.writes).push(record);
      });
    }};
  }, holdWrites);
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  return {panel, errors};
}

async function settle(page, collection, index, failed, efforts) {
  await page.evaluate(({collection,index,failed,efforts}) => ruleAsyncOwner[collection][index].finish(failed, efforts), {collection,index,failed,efforts});
  // Drain the actual promise callbacks and native control publication before
  // comparing state; the held transport is the only production seam replaced.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function retireOwner(panel, kind) {
  await panel.evaluate((host, kind) => {
    if (kind === "user" || kind === "ABA") host.hass = {...host.hass, user:{id:"rule-owner-B", is_admin:true}};
    if (kind === "ABA") host.hass = {...host.hass, user:{id:"rule-owner-A", is_admin:true}};
    if (kind === "session") host._clearConfigDraft();
  }, kind);
}

async function reopen(panel) {
  await panel.locator("#rule-dialog").evaluate(dialog => dialog.close());
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
}

async function snapshot(panel) {
  return panel.evaluate(host => ({
    choices:host.shadowRoot.querySelector("#rule-reasoning").innerHTML,
    catalog:host._modelCatalogData || null,
    model:host.shadowRoot.querySelector("#rule-model").value,
    error:host.shadowRoot.querySelector("#rule-error").textContent,
    toasts:ruleAsyncOwner.toasts,
  }));
}

for (const bundled of [false,true]) for (const change of ["user","ABA","session","visit"]) for (const failed of [false,true]) {
  test(`Rule lookup retires ${change} callback, failure=${failed} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const {panel,errors} = await prepare(page,bundled);
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("held-old-model");
    await expect.poll(() => page.evaluate(() => ruleAsyncOwner.lookups.length)).toBe(1);
    if (change === "visit") await reopen(panel);
    else await retireOwner(panel,change);
    const before = await snapshot(panel);
    await settle(page,"lookups",0,failed,["old-only"]);
    expect(await snapshot(panel)).toEqual(before);
    expect(await page.evaluate(() => ruleAsyncOwner.lookups.length)).toBe(1);
    await expectHarnessClean(page,errors);
  });
}

for (const bundled of [false,true]) for (const supersession of ["local","empty","newer-model"]) {
  test(`Rule lookup latest intent survives ${supersession} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const {panel,errors} = await prepare(page,bundled);
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("held-old-model");
    await expect.poll(() => page.evaluate(() => ruleAsyncOwner.lookups.length)).toBe(1);
    if (supersession === "local") await panel.locator("#rule-action-type").selectOption("local_action");
    if (supersession === "empty") await panel.locator("#rule-model").fill("");
    if (supersession === "newer-model") {
      await panel.locator("#rule-model").fill("held-new-model");
      await expect.poll(() => page.evaluate(() => ruleAsyncOwner.lookups.length)).toBe(2);
      await settle(page,"lookups",1,false,["low"]);
      expect((await snapshot(panel)).catalog.requested_model).toBe("held-new-model");
    }
    const before = await snapshot(panel);
    await settle(page,"lookups",0,false,["old-only"]);
    expect(await snapshot(panel)).toEqual(before);
    expect(await page.evaluate(() => ruleAsyncOwner.lookups.length)).toBe(supersession === "newer-model" ? 2 : 1);
    await expectHarnessClean(page,errors);
  });
}

for (const bundled of [false,true]) for (const change of ["user","ABA","session","visit"]) for (const failed of [false,true]) {
  test(`Rule submit settles without publishing to ${change}, failure=${failed} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const {panel,errors} = await prepare(page,bundled,true);
    await panel.locator("#rule-name").fill("Submitted source Rule");
    await panel.locator("#rule-phrases").fill("held source command");
    await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => {selector.value=[{action:"light.turn_on", target:{entity_id:"light.kitchen"}}];});
    await panel.locator("#rule-save").click();
    await expect.poll(() => page.evaluate(() => ruleAsyncOwner.writes.length)).toBe(1);
    if (change !== "visit") await retireOwner(panel,change);
    await reopen(panel);
    await panel.locator("#rule-name").fill("New target Rule draft");
    const before = await panel.evaluate(host => ({result:structuredClone(host._result),error:host.shadowRoot.querySelector("#rule-error").textContent,
      disabled:host.shadowRoot.querySelector("#rule-save").disabled}));
    await settle(page,"writes",0,failed);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open",true);
    await expect(panel.locator("#rule-name")).toHaveValue("New target Rule draft");
    expect(await panel.evaluate(host => ({result:host._result,error:host.shadowRoot.querySelector("#rule-error").textContent,
      disabled:host.shadowRoot.querySelector("#rule-save").disabled}))).toEqual(before);
    expect(await page.evaluate(() => ruleAsyncOwner.writes.length)).toBe(1);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && ["create","update"].includes(call.action)).length)).toBe(failed ? 0 : 1);
    await expectHarnessClean(page,errors);
  });
}
