import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the genuine Home Assistant backend bridge");

test("Broadcast retries a lost actual queue acknowledgement with the original operation identity", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(`/tests_browser/real-ha-fixture.html?route=overview&backend=${encodeURIComponent(backendUrl)}`);
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#broadcast-message")).toBeVisible();
  await panel.locator('[data-broadcast-entity="assist_satellite.kitchen"]').check();
  await panel.evaluate(host => {
    const original = host._hass.callWS;
    host._retryActualRequests = [];
    host._retryActualAdmissions = [];
    host._hass.callWS = async request => {
      const result = await original(request);
      if (request.type?.endsWith("/broadcast") && request.action === "send") {
        host._retryActualRequests.push(request.operation_id);
        host._retryActualAdmissions.push(result.id);
        if (host._retryActualRequests.length === 1) throw {code:"unknown_error", message:"Lost acknowledgement after actual admission"};
      }
      return result;
    };
  });
  await panel.locator("#broadcast-message").fill("Retry witness");
  await panel.locator("#broadcast-send").click();
  await expect(panel.locator("#broadcast-send")).toBeEnabled();
  await expect(panel.locator("#broadcast-message")).toHaveValue("Retry witness");
  await panel.locator("#broadcast-send").click();
  await expect(panel.locator("#broadcast-message")).toHaveValue("");
  const state = await panel.evaluate(host => ({requests:host._retryActualRequests, admissions:host._retryActualAdmissions, history:host._eocBroadcastSnapshot.history.filter(item => item.message === "Retry witness")}));
  expect(state.requests).toHaveLength(2);
  expect(state.requests[0]).toEqual(expect.any(String));
  expect(state.requests[0]).toBe(state.requests[1]);
  expect(state.admissions[0]).toBe(state.admissions[1]);
  expect(state.history).toHaveLength(1);
  await expectHarnessClean(page, errors);
});

for (const bundle of [false, true]) {
  test(`Broadcast retires an actual admitted message before the next click (${bundle ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(`/tests_browser/real-ha-fixture.html?route=overview&backend=${encodeURIComponent(backendUrl)}&bundle=${bundle ? "1" : "0"}`);
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#broadcast-message")).toBeVisible();
    const priorHistoryCount = await panel.evaluate(host => host._eocBroadcastSnapshot.history.length);
    await panel.locator('[data-broadcast-entity="assist_satellite.kitchen"]').check();
    await panel.evaluate((host, failRefresh) => {
      const original = host._hass.callWS;
      host._testBroadcastSends = 0;
      host._hass.callWS = async message => {
        if (message.type?.endsWith("/broadcast") && message.action === "snapshot" && host._testFailBroadcastRefresh) {
          throw Error("controlled history refresh failure");
        }
        const response = await original(message);
        if (message.type?.endsWith("/broadcast") && message.action === "send") {
          host._testBroadcastSends++;
          host._testBroadcastAck = response;
          host._testFailBroadcastRefresh = failRefresh;
        }
        return response;
      };
    }, !bundle);
    await panel.locator("#broadcast-message").fill(`Dinner ${bundle ? "bundle" : "source"}`);
    await panel.locator("#broadcast-send").click();
    if (!bundle) {
      // A failed refresh replaces the card with an error. Return through the
      // normal navigation path before exercising the next composer action.
      await expect(panel.locator("#broadcast-card [role=alert]")).toContainText("controlled history refresh failure");
      expect(await panel.evaluate(host => host._broadcastMessage)).toBe("");
      await panel.evaluate(host => host._testFailBroadcastRefresh = false);
      await panel.getByRole("button", {name:"Guide", exact:true}).click();
      await panel.getByRole("button", {name:"Overview", exact:true}).click();
    }
    await expect(panel.locator("#broadcast-message")).toHaveValue("");
    await expect(panel.locator("#broadcast-send")).toBeEnabled();
    const response = await panel.evaluate(host => host._testBroadcastAck);
    expect(response.id).toEqual(expect.any(String));
    expect(response.targets).toEqual(["assist_satellite.kitchen"]);
    expect(response).not.toHaveProperty("status");
    await panel.locator("#broadcast-send").click();
    expect(await panel.evaluate(host => host._testBroadcastSends)).toBe(1);
    await panel.evaluate(host => host._testFailBroadcastRefresh = false);
    await panel.locator("#broadcast-refresh").click();
    await expect(panel.locator(".broadcast-history")).toContainText("Delivered");
    await expect(panel.locator(".broadcast-history-item")).toHaveCount(Math.min(10, priorHistoryCount + 1));
    await expectHarnessClean(page, errors);
  });
}
