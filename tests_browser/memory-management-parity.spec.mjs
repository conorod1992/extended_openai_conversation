import {test, expect} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection, beginDataMeasure, finishDataMeasure} from "./data-collection-helpers.mjs";
let errors;
test.beforeEach(async ({page}) => { errors = trackPageErrors(page); });
test.afterEach(async ({page}) => { await expectHarnessClean(page, errors); });

for (const bundled of [false, true]) {
  test(`rich Memory editing preserves metadata, confirmation, keyed identity and search (${bundled})`, async ({page}) => {
    const panel = await openDataCollection(page, "persistent", 80, bundled);
    await page.evaluate(async () => {
      Object.assign(dataCollectionBackend.state.memories[3], {subject: "Bins", key: "bins.day", valid_from: "2026-09-01T00:00:00Z", last_confirmed_at: "2026-09-01T00:00:00Z"});
      await browserHarness.panel._loadSection(true);
      browserHarness.panel._bindActions(); browserHarness.panel._bindActions();
    });
    await panel.locator("#list-search").fill("Memory");
    await page.waitForFunction(() => browserHarness.panel._managementBrowserState.memoryQuery === "memory");
    await panel.locator('[data-memory-id="memory-3"] .memory-edit-button').click();
    await expect(panel.locator("#memory-subject")).toHaveValue("Bins");
    await expect(panel.locator("#memory-key")).toHaveValue("bins.day");
    await expect(panel.locator("#memory-valid-from")).toHaveValue("2026-09-01T00:00:00Z");
    await expect(panel.locator("#memory-importance")).toHaveValue("low");
    await expect(panel.locator("#memory-refresh-confirmation")).not.toBeChecked();
    await panel.locator("#memory-content").fill("Memory updated");
    await panel.locator("#memory-category").fill("chores");
    await panel.locator("#memory-importance").selectOption("high");
    await panel.locator("#memory-subject").fill("");
    await beginDataMeasure(page);
    await panel.locator("#memory-save").click();
    await expect(panel.locator('[data-memory-id="memory-3"]')).toContainText("Memory updated");
    expect(await finishDataMeasure(page)).toMatchObject({retainedCards: 79, initialCards: 80, mainChildReplacements: 0, inputRetained: true});
    await expect(panel.locator("#list-search")).toHaveValue("Memory");
    const calls = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "update"));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({importance: "high", key: "bins.day", valid_from: "2026-09-01T00:00:00Z", clear_fields: ["subject"], expected_revision: 1, refresh_confirmation: false});
    expect(await page.evaluate(() => dataCollectionBackend.state.memories[3].last_confirmed_at)).toBe("2026-09-01T00:00:00Z");
    await panel.locator('[data-memory-id="memory-3"] .memory-edit-button').click();
    await expect(panel.locator("#memory-subject")).toHaveValue("");
    await expect(panel.locator("#memory-importance")).toHaveValue("high");
    await panel.locator("#memory-refresh-confirmation").check();
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open", true);
    expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "update").at(-1).refresh_confirmation)).toBe(true);
  });
}

test("Memory revision conflict protects open editor and refresh/reopen uses fresh revision", async ({page}) => {
  const panel = await openDataCollection(page, "persistent");
  await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
  await panel.locator("#memory-content").fill("Stale draft");
  await page.evaluate(async () => {
    Object.assign(dataCollectionBackend.state.memories[1], {content: "Newer content", revision: 2});
    await browserHarness.panel._loadSection(true);
  });
  await expect(panel.locator("#memory-content")).toHaveValue("Stale draft");
  expect(await panel.evaluate(host => host._editingMemory.revision)).toBe(1);
  await panel.locator("#memory-save").click();
  await expect(panel.locator("#memory-error")).toContainText("changed since it was loaded");
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  expect(await page.evaluate(() => dataCollectionBackend.state.memories[1].content)).toBe("Newer content");
  await panel.locator("#memory-dialog .dialog-actions .close-editor").click();
  await panel.evaluate(host => host._loadSection(true));
  await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
  await expect(panel.locator("#memory-content")).toHaveValue("Newer content");
  await panel.locator("#memory-content").fill("Reopened edit");
  await panel.locator("#memory-save").click();
  await expect(panel.locator('[data-memory-id="memory-1"]')).toContainText("Reopened edit");
});

test("Memory moves use one update, preserve ID, and reconcile both directions", async ({page}) => {
  const panel = await openDataCollection(page, "persistent", 20);
  await page.evaluate(() => {
    const host = browserHarness.panel, original = browserHarness.hass.callWS;
    const scopes = [...host._data.scopes, {scope_id: "shared:household", scope_type: "shared", display_name: "Shared household", memory_count: 1}];
    browserHarness.hass.callWS = message => message.section === "scopes" ? Promise.resolve({scopes}) : original(message);
    host._data.scopes = scopes; host._baseScopes = scopes; host._scopeCatalogCache.clear(); host._eocScopeCatalogTimes.clear(); host._render();
  });
  await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
  await panel.locator("#memory-owner").selectOption("shared:household");
  await beginDataMeasure(page); await panel.locator("#memory-save").click();
  await expect(panel.locator('[data-memory-id="memory-1"]')).toHaveCount(0);
  expect(await finishDataMeasure(page)).toMatchObject({retainedCards: 19, mainChildReplacements: 0});
  await panel.locator("#scope").selectOption("shared:household");
  await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
  await expect(panel.locator("#memory-owner")).toHaveValue("shared:household");
  await panel.locator("#memory-owner").selectOption("user:test-user");
  await panel.locator("#memory-save").click();
  await expect(panel.locator('[data-memory-id="memory-1"]')).toHaveCount(0);
  expect(await page.evaluate(() => dataCollectionBackend.state.memories.filter(m => m.memory_id === "memory-1"))).toHaveLength(1);
  const calls = await page.evaluate(() => dataCollectionBackend.calls.filter(call => ["update", "delete", "add"].includes(call.action)));
  expect(calls.map(c => [c.action, c.scope_id, c.target_scope_id])).toEqual([["update", "user:test-user", "shared:household"], ["update", "shared:household", "user:test-user"]]);
});

test("Temporary clear requires confirmation, ignores search and preserves foreign scope and persistent data", async ({page}) => {
  const panel = await openDataCollection(page, "temporary");
  await page.evaluate(() => dataCollectionBackend.state.temporary.push({...dataCollectionBackend.state.temporary[0], memory_id: "foreign", scope_id: "shared:household"}));
  await panel.locator("#list-search").fill("Temporary 1");
  await panel.locator("#clear-temporary").click();
  await panel.locator("#confirm-cancel").click();
  expect(await page.evaluate(() => dataCollectionBackend.calls.filter(c => c.action === "temporary_clear"))).toHaveLength(0);
  await panel.evaluate(host => { host._bindActions(); host._bindActions(); });
  await panel.locator("#clear-temporary").click();
  await beginDataMeasure(page); await acceptConfirmation(panel);
  await expect(panel.locator(".memory-list article")).toHaveCount(0);
  expect(await finishDataMeasure(page)).toMatchObject({mainChildReplacements: 0, inputRetained: true});
  await expect(panel.locator("#list-search")).toHaveValue("Temporary 1");
  const result = await page.evaluate(() => ({calls: dataCollectionBackend.calls.filter(c => c.action === "temporary_clear"), temporary: dataCollectionBackend.state.temporary, persistent: dataCollectionBackend.state.memories.length}));
  expect(result.calls).toHaveLength(1);
  expect(result.calls[0]).toMatchObject({scope_id: "user:test-user", subentry_id: "agent-1", confirm: true});
  expect(result.temporary.map(m => m.memory_id)).toEqual(["foreign"]);
  expect(result.persistent).toBe(100);
});

