import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const cases = [
  ["Assistant", "assistant/basics", "configuration", "get"],
  ["Request Rules", "capabilities/request-rules", "request_rules", "list"],
  ["Function Tools", "capabilities/functions", "configuration", "get"],
  ["Function Groups", "capabilities/functions", "configuration", "get"],
  ["Memory", "data-memory/memories", "memories", "list"],
  ["Knowledge", "data-memory/knowledge", "knowledge", "list"],
  ["Guest Mode", "capabilities/guest-mode", "guest_mode", "get"],
  ["Quiet Hours", "capabilities/quiet-hours", "configuration", "get"],
];

for (const [surface, route, section, action] of cases) {
  test(`${surface} late read cannot repaint a newer route`, async ({page}, testInfo) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("data-memory/knowledge"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
    await page.evaluate(({section, action}) => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      let release;
      const gate = new Promise(resolve => { release = resolve; });
      window.__lateRead = {started: 0, release};
      browserHarness.hass.callWS = async request => {
        if (request.section === section && request.action === action) {
          window.__lateRead.started++;
          await gate;
        }
        return original(request);
      };
    }, {section, action});
    await page.evaluate(route => {
      history.pushState({}, "", `/extended-openai/${route}`);
      browserHarness.panel.route = {};
    }, route);
    await expect.poll(() => page.evaluate(() => window.__lateRead.started)).toBeGreaterThan(0);
    await page.evaluate(() => {
      history.pushState({}, "", "/extended-openai/data-memory/knowledge");
      browserHarness.panel.route = {};
    });
    await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
    await page.evaluate(() => window.__lateRead.release());
    await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
    await expect(page).toHaveURL(/\/extended-openai\/data-memory\/knowledge$/);
    await expect(panel).toHaveCount(1);
    await expectHarnessClean(page, errors);
    await testInfo.attach("stale-response-injection", {body: JSON.stringify({surface, route, section, action, injections: 1}), contentType: "application/json"});
    console.log(`ENHANCED STALE_RESPONSE surface=${JSON.stringify(surface)} injections=1`);
  });
}
