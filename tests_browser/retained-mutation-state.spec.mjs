import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["knowledge", "persistent"]) test(`a late ${kind} acknowledgement revalidates a newer committed collection (${bundled ? "bundle" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, kind, 10, bundled);
  const knowledge = kind === "knowledge", section = knowledge ? "knowledge" : "memories";
  await panel.locator(knowledge ? '[data-source-id="source-1"] .source-edit-button' : '[data-memory-id="memory-1"] .memory-edit-button').click();
  await panel.locator(knowledge ? "#knowledge-content" : "#memory-content").fill("Committed A with held acknowledgement");
  await page.evaluate(section => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    let held = false;
    browserHarness.hass.callWS = async message => {
      const response = await original(message);
      if (!held && message.section === section && message.action === "update") {
        held = true;
        window.committedAcknowledgement = response;
        return new Promise(resolve => { window.releaseCommittedAcknowledgement = () => resolve(response); });
      }
      return response;
    };
  }, section);
  await panel.locator(knowledge ? "#knowledge-save" : "#memory-save").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseCommittedAcknowledgement))).toBe(true);
  await panel.evaluate(async (host, section) => {
    const response = committedAcknowledgement;
    await host._call(section, "update", section === "knowledge" ? {
      source_id: response.source.source_id, expected_revision: response.source.updated_at,
      title: "Newer committed title", description: response.source.description, enabled: true, content: "Newer committed B",
    } : {
      scope_id: host._scopeId, memory_id: response.memory.memory_id, expected_revision: response.memory.revision,
      content: "Newer committed B", category: response.memory.category,
    });
    await host._loadSection(true);
  }, section);
  const contents = () => panel.evaluate((host, knowledge) => host._result[knowledge ? "sources" : "memories"].map(record => record.content), knowledge);
  expect(await contents()).toContain("Newer committed B");
  const reads = await page.evaluate(section => dataCollectionBackend.calls.filter(call => call.section === section && call.action === "list").length, section);
  await page.evaluate(() => releaseCommittedAcknowledgement());
  await expect(panel.locator(knowledge ? "#knowledge-dialog" : "#memory-dialog")).not.toBeVisible();
  await expect.poll(() => page.evaluate(section => dataCollectionBackend.calls.filter(call => call.section === section && call.action === "list").length, section)).toBeGreaterThan(reads);
  expect(await contents()).toContain("Newer committed B");
  expect(await contents()).not.toContain("Committed A with held acknowledgement");
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
