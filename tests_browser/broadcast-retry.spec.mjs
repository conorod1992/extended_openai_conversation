import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean, acceptConfirmation} from "./browser-helpers.mjs";

for (const changed of [false, true]) test(`Broadcast retains an uncertain operation across refresh and ${changed ? "requires a new-send confirmation for edits" : "retries the same admission"}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#broadcast-enabled").check();
  await expect(panel.locator("#broadcast-refresh")).toBeVisible();
  await panel.evaluate(host => {
    const original = host._hass.callWS.bind(host._hass);
    host._retryRequests = [];
    host._retryAdmissions = new Map();
    host._retrySnapshotReads = 0;
    host._hass.callWS = async request => {
      if (!request.type?.endsWith("/broadcast")) return original(request);
      if (request.action === "snapshot") return {enabled:true, can_manage:true, retry_token:`${"a".repeat(32)}:${1234 + ++host._retrySnapshotReads}`, catalog:{satellites:[{id:"assist_satellite.kitchen", name:"Kitchen", state:"idle"}], areas:[]}, history:[], pending:[]};
      if (request.action === "send") {
        host._retryRequests.push(request);
        if (host._retryAdmissions.has(request.operation_id)) return host._retryAdmissions.get(request.operation_id);
        const result = {id:`admission-${host._retryAdmissions.size + 1}`, targets:request.entity_ids};
        host._retryAdmissions.set(request.operation_id, result);
        if (host._retryRequests.length === 1) throw {code:"unknown_error", message:"Connection lost after admission"};
        return result;
      }
      return original(request);
    };
  });
  await panel.locator("#broadcast-refresh").click();
  await panel.locator('[data-broadcast-entity="assist_satellite.kitchen"]').check();
  await panel.locator("#broadcast-message").fill("Dinner");
  await panel.locator("#broadcast-send").click();
  await expect(panel.locator("#broadcast-message")).toHaveValue("Dinner");
  await expect(panel.locator("#broadcast-retry-status")).toBeVisible();
  await expect(panel.locator("#broadcast-retry-status")).toContainText("Sending as new can repeat");
  expect(await panel.evaluate(host => host._retryRequests[0].operation_id.split(":")[1])).toBe("1236");
  await panel.locator("#broadcast-refresh").click();
  await expect(panel.locator("#broadcast-retry-status")).toBeVisible();
  if (changed) await panel.locator("#broadcast-message").fill("New message");
  await panel.locator("#broadcast-send").click();
  if (changed) {
    await expect(panel.locator("#confirm-message")).toContainText("may already have been queued");
    expect(await panel.evaluate(host => host._retryRequests.length)).toBe(1);
    await acceptConfirmation(panel);
  }
  await expect(panel.locator("#broadcast-message")).toHaveValue("");
  await expect(panel.locator("#broadcast-retry-status")).toBeHidden();
  const state = await panel.evaluate(host => ({ids:host._retryRequests.map(request => request.operation_id), admissions:host._retryAdmissions.size, attempt:host._broadcastAttempt}));
  expect(state.ids).toHaveLength(2);
  expect(state.ids.every(id => typeof id === "string" && id.length > 60)).toBe(true);
  expect(state.ids[0] === state.ids[1]).toBe(!changed);
  expect(state.admissions).toBe(changed ? 2 : 1);
  expect(state.attempt).toBe(null);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`Broadcast retains admitted send ownership across account changes (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  for (const whileAway of [false, true]) {
    await page.goto(fixtureUrl("overview", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator("#broadcast-enabled").check();
    await expect(panel.locator("#broadcast-message")).toBeVisible();
    await panel.evaluate(host => {
      host.hass = {...host.hass, user:{id:"broadcast-owner", is_admin:true}};
      const original = host.hass.callWS.bind(host.hass);
      window.heldBroadcast = {owner:host.hass.user.id, sends:0};
      host.hass.callWS = async request => {
        if (!request.type?.endsWith("/broadcast")) return original(request);
        if (request.action === "snapshot") return {enabled:true, can_manage:true, retry_token:`${"a".repeat(32)}:1234`, catalog:{satellites:[{id:"assist_satellite.kitchen", name:"Kitchen"}], areas:[]}, history:[], pending:[]};
        if (request.action !== "send") return original(request);
        heldBroadcast.sends++;
        return new Promise(resolve => { heldBroadcast.finish = () => resolve({id:"held-admission", targets:request.entity_ids}); });
      };
    });
    await panel.locator("#broadcast-refresh").click();
    await panel.locator('[data-broadcast-entity="assist_satellite.kitchen"]').check();
    await panel.locator("#broadcast-message").fill("Retained admitted announcement");
    await panel.locator("#broadcast-send").click();
    await expect.poll(() => page.evaluate(() => heldBroadcast.sends)).toBe(1);
    await panel.evaluate(host => { host.hass.user.id = "different-account"; host.hass = host.hass; });
    await expect(panel.locator("#broadcast-message")).toHaveCount(0);
    if (whileAway) {
      await page.evaluate(() => heldBroadcast.finish());
      await expect.poll(() => panel.evaluate(host => host._broadcastSending)).toBe(false);
    }
    await panel.evaluate(host => { host.hass.user.id = heldBroadcast.owner; host.hass = host.hass; });
    if (!whileAway) await page.evaluate(() => heldBroadcast.finish());
    await expect.poll(() => panel.evaluate(host => host._broadcastSending)).toBe(false);
    // Account changes retire cached catalogues. An authoritative refresh makes
    // the retained composer actionable without replacing its operation owner.
    await panel.locator("#broadcast-refresh").click();
    await expect(panel.locator("#broadcast-send")).toBeEnabled();
    await expect(panel.locator("#broadcast-message")).toHaveValue("Retained admitted announcement");
    await panel.locator("#broadcast-send").click();
    await expect(panel.locator("#confirm-message")).toContainText("may already have been queued");
    await panel.locator("#confirm-cancel").click();
    await expect(panel.locator("#broadcast-retry-status")).toBeVisible();
    expect(await page.evaluate(() => heldBroadcast.sends)).toBe(1);
  }
  await expectHarnessClean(page, errors);
});


for (const bundled of [false, true]) test(`Built-in Broadcast setting persists independently (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  const shortcut = panel.locator("#broadcast-builtin-voice-command");
  await expect(shortcut).toBeChecked();
  await expect(panel.locator("#broadcast-enabled")).not.toBeChecked();
  await shortcut.uncheck();
  await expect(shortcut).not.toBeChecked();
  await panel.locator("#broadcast-enabled").check();
  await expect(shortcut).not.toBeChecked();
  await panel.locator("#broadcast-message").fill("Retained draft");
  await shortcut.check();
  await expect(shortcut).toBeChecked();
  await expect(panel.locator("#broadcast-message")).toHaveValue("Retained draft");
  await expect(panel.locator("#broadcast-enabled")).toBeChecked();
  expect(await page.evaluate(() => window.browserHarness.calls.filter(call => call.action === "set_builtin_voice_command").map(call => call.enabled))).toEqual([false, true]);
  await expectHarnessClean(page, errors);
});
