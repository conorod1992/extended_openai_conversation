import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the genuine Home Assistant backend bridge");

for (const bundle of [false, true]) {
  test(`Broadcast retires an actual admitted message before the next click (${bundle ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(`/tests_browser/real-ha-fixture.html?route=overview&backend=${encodeURIComponent(backendUrl)}&bundle=${bundle ? "1" : "0"}`);
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#broadcast-message")).toBeVisible();
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
    await expect(panel.locator(".broadcast-history-item")).toHaveCount(bundle ? 2 : 1);
    await expectHarnessClean(page, errors);
  });
}
