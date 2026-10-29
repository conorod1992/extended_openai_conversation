import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const guest of [true, false]) for (const partial of [false, true]) test(`${guest ? "Guest" : "Quiet"} policy ACK preserves known mutable fields: partial=${partial} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const route = guest ? "capabilities/guest-mode" : "capabilities/quiet-hours";
  await page.goto(fixtureUrl(route, bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(guest ? "#guest-web-search" : "#qh-enabled")).toBeAttached();
  await panel.evaluate(async (host, {guest, partial}) => {
    const send = host.hass.callWS.bind(host.hass);
    const section = guest ? "guest_mode" : "quiet_hours", action = guest ? "save_policy" : "update";
    const initial = browserHarness.getState()[guest ? "guest" : "quiet"];
    const defaults = structuredClone(initial.config);
    const config = {...initial.config, ...(guest ? {guest_mode_enabled:true} : {start:"23:15"})};
    await send({type:"extended_openai_conversation_responses/management", section, action,
      entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId, revision:initial.revision, config});
    window.policyConfigWrites = [];
    window.policyConfigRevision = "quiet-review-1";
    host.hass.callWS = async request => {
      if (request.section !== section) return send(request);
      if (!guest && request.action === "get") return {...await send(request), revision:policyConfigRevision};
      if (request.action !== action) return send(request);
      policyConfigWrites.push(structuredClone(request));
      const stored = browserHarness.getState()[guest ? "guest" : "quiet"];
      const revision = guest ? stored.revision : policyConfigRevision;
      if (request.revision != null && request.revision !== revision) throw {code:"invalid_request", message:"Policy changed before save"};
      // Guest's real save_policy is a configuration patch. Quiet's real
      // async_update_config normalizes a whole policy using declared defaults.
      const prepared = guest ? {...stored.config, ...request.config} : {...defaults, ...request.config};
      const result = await send({...request, revision, config:prepared});
      if (!guest) {policyConfigRevision += "x"; result.revision = policyConfigRevision;}
      if (partial && policyConfigWrites.length === 1) delete result.config[guest ? "guest_mode_enabled" : "start"];
      return result;
    };
    host._sectionCache.clear(); await host._loadSection(true);
  }, {guest, partial});
  const control = panel.locator(guest ? "#guest-web-search" : "#qh-enabled");
  await control.evaluate(input => {for (let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS")p.open=true;});
  await control.check();
  await panel.locator("#save-page").click();
  const view = guest ? "capabilities/guest-mode" : "capabilities/quiet-hours";
  await expect.poll(() => panel.evaluate((host, view) => host._unsavedState.scopes.get(view).pending, view)).toBe(false);
  // The next deliberate, unrelated action must not rewrite a known field
  // omitted only by the first response's presentation envelope.
  await control.uncheck();
  if (!guest) await panel.locator("#qh-end").fill("08:15");
  await panel.locator("#save-page").click();
  await expect.poll(() => panel.evaluate((host, view) => host._unsavedState.scopes.get(view).pending, view)).toBe(false);
  const stored = await page.evaluate(guest => browserHarness.getState()[guest ? "guest" : "quiet"].config, guest);
  if (guest) expect(stored.guest_mode_enabled).toBe(true);
  else expect(stored.start).toBe("23:15");
  // Also verify that a successful-looking ACK has not published a false local
  // permission/time baseline even where Guest patch semantics preserve storage.
  if (guest) await expect(panel.locator("#guest-controls-enabled")).toBeChecked();
  else await expect(panel.locator("#qh-start")).toHaveValue("23:15");
  await page.goto(fixtureUrl(route, bundled ? "&bundle=1" : ""));
  if (guest) await expect(panel.locator("#guest-controls-enabled")).toBeChecked();
  else await expect(panel.locator("#qh-start")).toHaveValue("23:15");
});
