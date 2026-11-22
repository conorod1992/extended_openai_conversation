import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const panelFor = (page) => page.locator("extended-openai-management-panel");
test("nightly Live Request confirmation, repeated activation, results, errors, stale navigation, and preview independence", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = panelFor(page);
  const live = panel.locator("#eoc-rule-live-test");
  await live.locator("summary").click();
  const state = await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    const original = panel._call.bind(panel);
    let releaseLocal;
    let releaseLate;
    const localPending = new Promise((resolve) => { releaseLocal = resolve; });
    const latePending = new Promise((resolve) => { releaseLate = resolve; });
    const calls = window.nightlyLiveCalls = [];
    panel._call = async (section, action, data) => {
      calls.push({section,action,text:data?.text});
      if (section !== "request_rules") return original(section,action,data);
      if (action === "test_match") return {matched:false};
      if (action !== "test") return original(section,action,data);
      if (data.text === "local success") return localPending;
      if (data.text === "late stale") return latePending;
      if (data.text === "AI success") return {response:"Provider replied",handled_locally:false,conversation_id:"nightly-conversation",matched_rule:null};
      if (data.text === "provider failure") throw new Error("Provider request failed");
      return {response:"Done",handled_locally:true,conversation_id:"nightly-local",matched_rule:{name:"Nightly local action"}};
    };
    window.releaseNightlyLocal = () => releaseLocal({response:"Local action completed",handled_locally:true,conversation_id:"nightly-local",matched_rule:{name:"Nightly local action"}});
    window.releaseNightlyLate = () => releaseLate({response:"Late result",handled_locally:true,conversation_id:"nightly-late",matched_rule:{name:"Nightly late action"}});
    return true;
  });
  expect(state).toBe(true);

  await panel.locator("#eoc-rule-live-text").fill("cancel this");
  await panel.locator("#eoc-rule-live-run").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-cancel").click();
  expect(await page.evaluate(() => window.nightlyLiveCalls.filter((call) => call.action === "test").length)).toBe(0);

  await panel.locator("#rule-match-test-text").fill("safe preview first");
  await panel.locator("#rule-match-test").click();
  await expect(panel.locator("#rule-match-test-result")).toContainText("No Request Rule matched");
  const safeResult = await panel.locator("#rule-match-test-result").evaluate((element) => element.textContent);

  await panel.locator("#eoc-rule-live-text").fill("local success");
  await panel.locator("#eoc-rule-live-run").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#eoc-rule-live-run")).toBeDisabled();
  await panel.locator("#eoc-rule-live-run").dispatchEvent("click");
  expect(await page.evaluate(() => window.nightlyLiveCalls.filter((call) => call.action === "test").length)).toBe(1);
  await page.evaluate(() => window.releaseNightlyLocal());
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Local action completed");
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Handled locally");
  await expect(panel.locator("#rule-match-test-result")).toHaveJSProperty("textContent", safeResult);

  await panel.locator("#eoc-rule-live-text").fill("AI success");
  await panel.locator("#eoc-rule-live-run").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Provider replied");
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("AI provider");

  await panel.locator("#eoc-rule-live-text").fill("provider failure");
  await panel.locator("#eoc-rule-live-run").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Provider request failed");
  await expect(panel.locator("#eoc-rule-live-run")).toBeEnabled();

  await panel.locator("#eoc-rule-live-text").fill("late stale");
  await panel.locator("#eoc-rule-live-run").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => window.nightlyLiveCalls.filter((call) => call.text === "late stale").length)).toBe(1);
  await panel.locator('.top-nav button[data-page="overview"]').click();
  await expect(panel.locator('.top-nav button[data-page="overview"]')).toHaveAttribute("aria-current", "page");
  await page.evaluate(() => window.releaseNightlyLate());
  expect(await page.evaluate(() => window.nightlyLiveCalls.filter((call) => call.action === "test").length)).toBe(4);
  await expectHarnessClean(page, errors);
});
