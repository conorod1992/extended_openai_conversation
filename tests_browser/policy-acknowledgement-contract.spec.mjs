import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const missingRevision of [false, true]) test(`Guest acknowledgement retains concurrency ownership: missing revision=${missingRevision} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  await page.goto(fixtureUrl("capabilities/guest-mode", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#guest-web-search")).toBeAttached();
  await panel.evaluate((host, missingRevision) => {
    const send = host.hass.callWS.bind(host.hass);
    window.policyReviewAttempts = [];
    // Management's production revision helper accepts omitted legacy revisions,
    // while rejecting a supplied stale revision. The fixture normally requires
    // every revision, so adapt only its legacy omission behavior here.
    host.hass.callWS = async request => {
      if (request.section !== "guest_mode" || request.action !== "save_policy") return send(request);
      policyReviewAttempts.push(structuredClone(request));
      const saved = browserHarness.getState().guest;
      if (request.revision != null && request.revision !== saved.revision) throw {code:"invalid_request", message:"Saved data changed; reload before saving."};
      const result = await send({...request, revision:saved.revision});
      if (missingRevision && policyReviewAttempts.length === 1) delete result.revision;
      return result;
    };
    window.changeGuestConcurrently = async () => {
      const saved = browserHarness.getState().guest;
      await send({type:"extended_openai_conversation_responses/management", section:"guest_mode", action:"save_policy", entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId, revision:saved.revision, config:{...saved.config, guest_mode_enabled:true}});
    };
  }, missingRevision);
  await panel.locator("#guest-web-search").evaluate(input => {for (let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
  await panel.locator("#guest-web-search").check();
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().guest.config.guest_web_search)).toBe(true);
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/guest-mode").pending)).toBe(false);
  await page.evaluate(() => changeGuestConcurrently());
  await panel.locator("#guest-web-search").uncheck();
  await panel.locator("#guest-separate-control").evaluate(input => {for (let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
  await panel.locator("#guest-separate-control").check();
  await panel.locator("#save-page").click();
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/guest-mode").pending)).toBe(false);
  const stored = await page.evaluate(() => browserHarness.getState().guest);
  const attempts = await page.evaluate(() => policyReviewAttempts);
  expect(stored.config.guest_mode_enabled).toBe(true);
  expect(attempts).toHaveLength(2);
  expect(attempts[1].revision).toBeDefined();
  await page.goto(fixtureUrl("capabilities/guest-mode", bundled ? "&bundle=1" : ""));
  await expect(panel.locator("#guest-controls-enabled")).toBeChecked();
});

for (const bundled of [false, true]) for (const missingRevision of [false, true]) test(`Quiet acknowledgement retains an observed revision: missing revision=${missingRevision} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  await page.goto(fixtureUrl("capabilities/quiet-hours", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#qh-enabled")).toBeVisible();
  await panel.evaluate(async (host, missingRevision) => {
    const send = host.hass.callWS.bind(host.hass);
    window.quietReviewRevision = "quiet-1";
    window.quietReviewAttempts = [];
    host.hass.callWS = async request => {
      if (request.section !== "quiet_hours") return send(request);
      if (request.action === "get") return {...await send(request), revision:quietReviewRevision};
      if (request.action !== "update") return send(request);
      quietReviewAttempts.push(structuredClone(request));
      if (request.revision != null && request.revision !== quietReviewRevision) throw {code:"invalid_request", message:"Quiet Hours changed; reload before saving"};
      const result = await send(request); quietReviewRevision += "x";
      if (!missingRevision || quietReviewAttempts.length !== 1) result.revision = quietReviewRevision;
      return result;
    };
    window.changeQuietConcurrently = async () => {
      const saved = browserHarness.getState().quiet;
      await send({type:"extended_openai_conversation_responses/management", section:"quiet_hours", action:"update", config:{...saved.config, start:"23:15"}});
      quietReviewRevision += "x";
    };
    host._sectionCache.clear(); await host._loadSection(true);
  }, missingRevision);
  await panel.locator("#qh-enabled").check();
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().quiet.config.enabled)).toBe(true);
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/quiet-hours").pending)).toBe(false);
  await page.evaluate(() => changeQuietConcurrently());
  await panel.locator("#qh-enabled").uncheck();
  await panel.locator("#qh-start").fill("21:45");
  await panel.locator("#save-page").click();
  await expect.poll(() => panel.evaluate(host => host._unsavedState.scopes.get("capabilities/quiet-hours").pending)).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().quiet.config.start)).toBe("23:15");
  const attempts = await page.evaluate(() => quietReviewAttempts);
  expect(attempts).toHaveLength(2);
  expect(attempts[1].revision).toBeDefined();
  await page.goto(fixtureUrl("capabilities/quiet-hours", bundled ? "&bundle=1" : ""));
  await expect(panel.locator("#qh-start")).toHaveValue("23:15");
});

for (const bundled of [false, true]) for (const guest of [true, false]) for (const field of (guest ? [false] : [false, true])) test(`${guest ? "Guest" : "Quiet"} malformed read cannot erase an observed ${field ? "field" : "revision"} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const route = guest ? "capabilities/guest-mode" : "capabilities/quiet-hours";
  await page.goto(fixtureUrl(route, bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(guest ? "#guest-web-search" : "#qh-enabled")).toBeAttached();
  await panel.evaluate(async (host, {guest, field}) => {
    const send = host.hass.callWS.bind(host.hass), section = guest ? "guest_mode" : "quiet_hours", action = guest ? "save_policy" : "update";
    let quietRevision = "read-quiet-1";
    window.policyReadWrites = [];
    window.breakPolicyRead = false;
    host.hass.callWS = async request => {
      if (request.section !== section) return send(request);
      const saved = browserHarness.getState()[guest ? "guest" : "quiet"];
      if (request.action === action) {
        policyReadWrites.push(structuredClone(request));
        const revision = guest ? saved.revision : quietRevision;
        if (request.revision != null && request.revision !== revision) throw {code:"invalid_request", message:"Concurrent policy changed"};
        const result = await send(guest ? {...request, revision:saved.revision} : request);
        if (!guest) {quietRevision += "x"; result.revision = quietRevision;}
        return result;
      }
      const result = await send(request);
      if (result.config) {
        if (!guest) result.revision = quietRevision;
        if (breakPolicyRead) {if (field) delete result.config.start; else delete result.revision;}
      }
      return result;
    };
    // Establish a revisioned Quiet server before testing a dropped revision.
    host._sectionCache.clear(); await host._loadSection(true);
    const concurrentChange = async () => {
      const saved = browserHarness.getState()[guest ? "guest" : "quiet"];
      await send({type:"extended_openai_conversation_responses/management", section, action,
        entry_id:host._selectedAgent().entry_id, subentry_id:host._agentId,
        ...(guest ? {revision:saved.revision} : {}),
        config:{...saved.config, ...(guest ? {guest_mode_enabled:true} : {start:"23:15"})}});
      if (!guest) quietRevision += "x";
    };
    if (field) await concurrentChange();
    window.breakPolicyRead = true;
    host._sectionCache.clear(); await host._loadSection(true);
    if (!field) await concurrentChange();
  }, {guest, field});
  const control = panel.locator(guest ? "#guest-web-search" : "#qh-enabled");
  if (await control.count()) {
    await control.evaluate(input => {for (let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
    await control.check();
    await panel.locator("#save-page").click();
    await expect.poll(() => panel.evaluate(host => [...host._unsavedState.scopes.values()].some(scope => scope.pending))).toBe(false);
  }
  const state = await page.evaluate(guest => browserHarness.getState()[guest ? "guest" : "quiet"], guest);
  expect(guest ? state.config.guest_mode_enabled : state.config.start).toBe(guest ? true : "23:15");
  expect(await page.evaluate(() => policyReadWrites)).toEqual([]);
  await page.evaluate(() => {window.breakPolicyRead = false;});
  await panel.getByRole("button", {name:"Retry", exact:true}).click();
  await expect(control).toBeAttached();
  await control.evaluate(input => {for (let p=input.parentElement;p;p=p.parentElement) if(p.tagName === "DETAILS") p.open=true;});
  await control.check();
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => policyReadWrites.length)).toBe(1);
  const fresh = await page.evaluate(guest => browserHarness.getState()[guest ? "guest" : "quiet"], guest);
  expect(guest ? fresh.config.guest_mode_enabled : fresh.config.start).toBe(guest ? true : "23:15");
  expect(guest ? fresh.config.guest_web_search : fresh.config.enabled).toBe(true);
});
