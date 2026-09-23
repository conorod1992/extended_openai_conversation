import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

test("a late Knowledge save cannot replace a newer route load", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "knowledge", 10);
  await panel.locator('[data-source-id="source-1"] .source-edit-button').click();
  await panel.locator("#knowledge-title").fill("Delayed title");
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS;
    browserHarness.hass.callWS = async message => {
      if (message.section === "knowledge" && message.action === "update") {
        await new Promise(resolve => { window.releaseKnowledgeSave = resolve; });
      }
      return original(message);
    };
  });
  await panel.locator("#knowledge-save").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseKnowledgeSave))).toBe(true);
  await panel.evaluate(host => host._loadSection(true));
  await page.evaluate(() => releaseKnowledgeSave());
  await expect.poll(() => page.evaluate(() => dataCollectionBackend.calls.some(call => call.action === "update"))).toBe(true);
  await expect(panel.locator('[data-source-id="source-1"]')).toContainText("Source 1");
  await expect(panel.locator('[data-source-id="source-1"]')).not.toContainText("Delayed title");
  await expectHarnessClean(page, errors);
});

test("clearing Usage details preserves aggregates, retention, and history selection", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/usage"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#clear-details")).toBeVisible();
  const before = await panel.evaluate(host => {
    host._usageHistoryWindow = "year";
    host._result = {
      ...host._result,
      summary: {...host._result.summary, latest: {total_tokens: 42}},
      days: {days: [{date: "2026-09-22", total_tokens: 100}], complete_history: true},
      runs: {runs: [{run_id: "run-1", total_tokens: 42}], total: 1},
      retention: {request_days: 12, run_days: 34},
    };
    host._render();
    const original = host._hass.callWS;
    window.usageCalls = [];
    host._hass.callWS = message => {
      if (message.section === "usage") usageCalls.push(message.action);
      if (message.section === "usage" && message.action === "clear_details") return Promise.resolve({deleted_runs: 1, deleted_requests: 2});
      return original(message);
    };
    return {summary: structuredClone(host._result.summary), days: structuredClone(host._result.days), retention: structuredClone(host._result.retention)};
  });
  await panel.locator("#clear-details").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => usageCalls.length)).toBe(1);
  const after = await panel.evaluate(host => ({
    summary: host._result.summary, days: host._result.days, retention: host._result.retention,
    runs: host._result.runs, window: host._usageHistoryWindow,
  }));
  expect(after.summary).toEqual({...before.summary, latest: null});
  expect(after.days).toEqual(before.days);
  expect(after.retention).toEqual(before.retention);
  expect(after.runs).toMatchObject({runs: [], total: 0});
  expect(after.window).toBe("year");
  expect(await page.evaluate(() => usageCalls)).toEqual(["clear_details"]);
  await expectHarnessClean(page, errors);
});

test("Temporary Memory delete and clear update only the selected scope count", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "temporary", 12);
  await panel.evaluate(host => {
    const scope = host._data.scopes.find(item => item.scope_id === host._scopeId);
    scope.temporary_memory_count = 12;
  });
  const initialReads = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_list").length);
  await panel.locator('[data-memory-id="temporary-1"] .delete-temporary').click();
  await acceptConfirmation(panel);
  await expect(panel.locator('[data-memory-id="temporary-1"]')).toHaveCount(0);
  expect(await panel.evaluate(host => host._data.scopes.find(item => item.scope_id === host._scopeId).temporary_memory_count)).toBe(11);
  await panel.locator("#clear-temporary").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".memory-list article")).toHaveCount(0);
  expect(await panel.evaluate(host => host._data.scopes.find(item => item.scope_id === host._scopeId).temporary_memory_count)).toBe(0);
  expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_list").length)).toBe(initialReads);
  await expectHarnessClean(page, errors);
});
