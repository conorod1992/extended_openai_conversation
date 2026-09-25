import {expect, test} from "@playwright/test";
import {mkdirSync, readFileSync, writeFileSync} from "node:fs";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const cases = [
  ["Assistant", "assistant/basics", "configuration", "get"],
  ["Request Rules", "capabilities/request-rules", "request_rules", "list"],
  ["Function Tools", "capabilities/functions", "configuration", "get"],
  ["Function Groups", "capabilities/functions", "configuration", "get"],
  ["Memory", "data-memory/memories", "memories", "list"],
  ["Knowledge", "data-memory/knowledge", "knowledge", "list"],
  ["Guest Mode", "capabilities/guest-mode", "guest_mode", "get"],
  ["Quiet Hours", "capabilities/quiet-hours", "quiet_hours", "get"],
];

for (const [surface, route, section, action] of cases) {
  test(`${surface} late read cannot repaint a newer route`, async ({page}, testInfo) => {
    const errors = trackPageErrors(page);
    const landingRoute = route === "data-memory/knowledge" ? "data-memory/memories" : "data-memory/knowledge";
    const landingHeading = route === "data-memory/knowledge" ? "Memories" : "Sources";
    await page.goto(fixtureUrl(landingRoute));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("heading", {name: landingHeading, exact: true})).toBeVisible();
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
    await page.evaluate(landingRoute => {
      history.pushState({}, "", `/extended-openai/${landingRoute}`);
      browserHarness.panel.route = {};
    }, landingRoute);
    await expect(panel.getByRole("heading", {name: landingHeading, exact: true})).toBeVisible();
    await page.evaluate(() => window.__lateRead.release());
    await expect(panel.getByRole("heading", {name: landingHeading, exact: true})).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/extended-openai/${landingRoute}$`));
    await expect(panel).toHaveCount(1);
    await expectHarnessClean(page, errors);
    await testInfo.attach("stale-response-injection", {body: JSON.stringify({surface, route, section, action, injections: 1}), contentType: "application/json"});
    const directory = process.env.STRESS_ARTIFACT_DIR || "stress-artifacts";
    mkdirSync(directory, {recursive: true});
    const path = `${directory}/browser-stale-responses.json`;
    let report = {staleResponses: 0, staleBySurface: {}};
    try { report = JSON.parse(readFileSync(path, "utf8")); } catch { /* First case. */ }
    report.staleResponses++;
    report.staleBySurface[surface] = (report.staleBySurface[surface] || 0) + 1;
    writeFileSync(path, JSON.stringify(report, null, 2));
    console.log(`ENHANCED STALE_RESPONSE surface=${JSON.stringify(surface)} injections=1`);
  });
}
