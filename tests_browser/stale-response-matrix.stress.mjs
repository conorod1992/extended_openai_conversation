import {expect, test} from "@playwright/test";
import {mkdirSync, readFileSync, writeFileSync} from "node:fs";
import {acceptConfirmation, browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

function recordInjection(surface, kind) {
  const directory = process.env.STRESS_ARTIFACT_DIR || "stress-artifacts";
  mkdirSync(directory, {recursive: true});
  const path = `${directory}/browser-stale-responses.json`;
  let report = {staleResponses: 0, staleBySurface: {}};
  try { report = JSON.parse(readFileSync(path, "utf8")); } catch { /* First case. */ }
  report.staleResponses++;
  report.staleBySurface[surface] = (report.staleBySurface[surface] || 0) + 1;
  report.staleByKind ||= {};
  report.staleByKind[kind] = (report.staleByKind[kind] || 0) + 1;
  writeFileSync(path, JSON.stringify(report, null, 2));
}

async function gateMutation(page, section, action) {
  await page.evaluate(({section, action}) => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    window.__lateMutation = {started: 0, release};
    browserHarness.hass.callWS = async request => {
      if (request.section === section && request.action === action) {
        window.__lateMutation.started++;
        const result = await original(request);
        await gate;
        return result;
      }
      return original(request);
    };
  }, {section, action});
}

async function changeRoute(page, route) {
  await page.evaluate(route => {
    history.pushState({}, "", `/extended-openai/${route}`);
    browserHarness.panel.route = {};
  }, route);
}

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
    recordInjection(surface, "read");
    console.log(`ENHANCED STALE_RESPONSE surface=${JSON.stringify(surface)} injections=1`);
  });
}

test("Assistant late save preserves a newer local draft", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await title.fill("Submitted Assistant title");
  await gateMutation(page, "configuration", "save");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => window.__lateMutation.started)).toBe(1);
  await title.fill("Newer unsaved Assistant title");
  await page.evaluate(() => window.__lateMutation.release());
  await expect(title).toHaveValue("Newer unsaved Assistant title");
  await expect(panel.locator("#save-config")).toBeEnabled();
  expect(await page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Submitted Assistant title");
  await expectHarnessClean(page, errors);
  recordInjection("Assistant", "save");
});

test("Knowledge late delete cannot resurrect the deleted source after navigation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Delete race source");
  await panel.locator("#knowledge-content").fill("Delete race content");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator(".list-card").filter({hasText: "Delete race source"})).toBeVisible();
  await gateMutation(page, "knowledge", "delete");
  await panel.locator(".list-card").filter({hasText: "Delete race source"}).locator(".delete-source").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => window.__lateMutation.started)).toBe(1);
  await changeRoute(page, "data-memory/memories");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await changeRoute(page, "data-memory/knowledge");
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await page.evaluate(() => window.__lateMutation.release());
  await expect(panel.locator(".list-card").filter({hasText: "Delete race source"})).toHaveCount(0);
  expect(await page.evaluate(() => browserHarness.getState().knowledgeSources.some(source => source.title === "Delete race source"))).toBe(false);
  await expectHarnessClean(page, errors);
  recordInjection("Knowledge", "delete");
});

test("Function late save cannot repaint a newer route", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-tool").click();
  await panel.locator("#tool-yaml").fill(browserToolYaml("Late Function save"));
  await gateMutation(page, "tools", "save");
  await panel.locator("#tool-save").click();
  await expect.poll(() => page.evaluate(() => window.__lateMutation.started)).toBe(1);
  await changeRoute(page, "data-memory/knowledge");
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await page.evaluate(() => window.__lateMutation.release());
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await changeRoute(page, "capabilities/functions");
  await expect(panel.locator(".tool-card").filter({hasText: "Late Function save"})).toBeVisible();
  expect((await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length))).toBe(1);
  await expectHarnessClean(page, errors);
  recordInjection("Function Tools", "save");
});

test("Guest policy late save cannot repaint a newer route", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-guest-mode="guest_knowledge_policy"]').selectOption("on");
  await gateMutation(page, "guest_mode", "save_policy");
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => window.__lateMutation.started)).toBe(1);
  await changeRoute(page, "data-memory/knowledge");
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await page.evaluate(() => window.__lateMutation.release());
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await changeRoute(page, "capabilities/guest-mode");
  await expect(panel.locator('[data-guest-mode="guest_knowledge_policy"]')).toHaveValue("on");
  expect((await page.evaluate(() => browserHarness.calls.filter(call => call.section === "guest_mode" && call.action === "save_policy").length))).toBe(1);
  await expectHarnessClean(page, errors);
  recordInjection("Guest Mode", "save");
});
