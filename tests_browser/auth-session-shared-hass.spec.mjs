import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const change of ["mutable user id ABA", "mutable logout return"]) {
  test(`shared hass ${change} retires native Rule lookup publication (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("button", {name:"Create rule", exact:true}).first()).toBeVisible();
    await panel.evaluate(host => {
      host.hass = {...host.hass, user:{id:"shared-hass-owner-A", is_admin:true}};
      host.hass = host.hass;
      const original = host.hass.callWS.bind(host.hass);
      const state = window.sharedHassOwner = {requests:[], toasts:[]};
      host._toast = (...args) => state.toasts.push(args);
      host.hass.callWS = request => {
        if (!request.type?.endsWith("/model_catalog") || request.action !== "lookup") return original(request);
        return new Promise((resolve, reject) => state.requests.push({request:structuredClone(request), finish:async failed => {
          if (failed) reject(Error("Retired shared-object lookup failed"));
          else resolve({...await original(request), reasoning_effort_options:["retired-only"]});
        }}));
      };
    });
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill("Retained draft across a shared hass mutation");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("held-shared-object-model");
    await expect.poll(() => page.evaluate(() => sharedHassOwner.requests.length)).toBe(1);
    await panel.evaluate((host, change) => {
      const same = host.hass, user = same.user, originalId = user.id;
      sharedHassOwner.payload = same;
      sharedHassOwner.result = host._result;
      sharedHassOwner.dialog = host.shadowRoot.querySelector("#rule-dialog");
      sharedHassOwner.selector = host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector");
      if (change === "mutable user id ABA") same.user.id = "shared-hass-owner-B";
      else delete same.user;
      host.hass = same;
      if (change === "mutable user id ABA") same.user.id = originalId;
      else same.user = user;
      host.hass = same;
      sharedHassOwner.before = {
        choices:host.shadowRoot.querySelector("#rule-reasoning").innerHTML,
        catalog:structuredClone(host._modelCatalogData || null),
        error:host.shadowRoot.querySelector("#rule-error").textContent,
      };
    }, change);
    await page.evaluate(async failed => {
      await sharedHassOwner.requests[0].finish(failed);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, change === "mutable logout return");
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#rule-name")).toHaveValue("Retained draft across a shared hass mutation");
    await expect(panel.locator("#rule-model")).toHaveValue("held-shared-object-model");
    expect(await panel.evaluate(host => ({
      choices:host.shadowRoot.querySelector("#rule-reasoning").innerHTML,
      catalog:host._modelCatalogData || null,
      error:host.shadowRoot.querySelector("#rule-error").textContent,
    }))).toEqual(await page.evaluate(() => sharedHassOwner.before));
    expect(await panel.evaluate(host => host.hass === sharedHassOwner.payload && host._result === sharedHassOwner.result
      && host.shadowRoot.querySelector("#rule-dialog") === sharedHassOwner.dialog
      && host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector") === sharedHassOwner.selector)).toBe(true);
    expect(await page.evaluate(() => sharedHassOwner.toasts)).toEqual([]);
    expect(await page.evaluate(() => sharedHassOwner.requests.length)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}
