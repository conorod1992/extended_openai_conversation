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
  expect(await panel.evaluate(host => host._retryRequests[0].operation_id.split(":")[1])).toBe("1236");
  await panel.locator("#broadcast-refresh").click();
  if (changed) await panel.locator("#broadcast-message").fill("New message");
  await panel.locator("#broadcast-send").click();
  if (changed) {
    await expect(panel.locator("#confirm-message")).toContainText("may already have been queued");
    expect(await panel.evaluate(host => host._retryRequests.length)).toBe(1);
    await acceptConfirmation(panel);
  }
  await expect(panel.locator("#broadcast-message")).toHaveValue("");
  const state = await panel.evaluate(host => ({ids:host._retryRequests.map(request => request.operation_id), admissions:host._retryAdmissions.size, attempt:host._broadcastAttempt}));
  expect(state.ids).toHaveLength(2);
  expect(state.ids.every(id => typeof id === "string" && id.length > 60)).toBe(true);
  expect(state.ids[0] === state.ids[1]).toBe(!changed);
  expect(state.admissions).toBe(changed ? 2 : 1);
  expect(state.attempt).toBe(null);
  await expectHarnessClean(page, errors);
});
