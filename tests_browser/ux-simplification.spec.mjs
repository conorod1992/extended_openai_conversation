import {test, expect} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) {
  for (const kind of ["persistent", "temporary"]) {
    test(`Add memory defaults from ${kind} and creates manual short-term memory (${bundled})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const panel = await openDataCollection(page, kind, 20, bundled);
      const originalCount = await panel.evaluate(host => host._data.scopes.find(scope => scope.scope_id === host._scopeId)?.temporary_memory_count || 0);
      await expect(panel.locator("#add-memory")).toBeVisible();
      await panel.locator("#add-memory").click();
      await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
      await expect(panel.locator("#memory-content")).toBeFocused();
      await expect(panel.locator("#memory-type")).toHaveValue(kind);
      await expect(panel.locator("#memory-type")).toBeEnabled();
      await expect(panel.locator("#memory-importance,#memory-refresh-confirmation,#memory-confirmation")).toHaveCount(0);
      await panel.locator("#memory-type").selectOption("persistent");
      await expect(panel.locator("#memory-expiry")).toBeHidden();
      await panel.locator("#memory-type").selectOption("temporary");
      await expect(panel.locator("#memory-expiry")).toBeVisible();
      await expect(panel.locator("#memory-expiry")).toHaveAttribute("type", "datetime-local");
      await panel.locator("#memory-content").fill("Manual short-term fact");
      await panel.locator("#memory-category").fill("home");
      await panel.locator("#memory-expiry").fill("2026-10-02T18:30");
      await panel.locator("#memory-save").click();
      await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open", true);
      const calls = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_add"));
      expect(calls).toHaveLength(1);
      expect(calls[0]).toMatchObject({content: "Manual short-term fact", expires_at: "2026-10-02T17:30:00.000Z", target_scope_id: "user:test-user"});
      expect(calls[0]).not.toHaveProperty("importance");
      expect(calls[0]).not.toHaveProperty("refresh_confirmation");
      expect(await panel.evaluate(host => host._data.scopes.find(scope => scope.scope_id === host._scopeId)?.temporary_memory_count)).toBe(originalCount + 1);
      expect(await page.evaluate(() => dataCollectionBackend.state.temporary.at(-1).source)).toBe("manual");
      if (kind === "persistent") await panel.locator('.memory-kind[data-kind="temporary"]').click();
      await expect(panel.locator(".memory-list")).toContainText("Manual short-term fact");
      await panel.locator('.list-card').filter({hasText: "Manual short-term fact"}).locator('.actions .edit-temporary-memory').click();
      await expect(panel.locator("#temporary-memory-expiry")).toHaveValue("2026-10-02T18:30");
      await expect(panel.locator("#temporary-memory-expiry")).toHaveAttribute("type", "datetime-local");
      await expect(panel.locator('#temporary-memory-dialog input[readonly]')).toHaveValue("Short-term");
      await panel.locator("#temporary-memory-expiry").press("Escape");
      await expect(panel.locator("#temporary-memory-dialog")).not.toHaveJSProperty("open", true);
      await expect(panel.locator("#confirm-dialog")).not.toHaveJSProperty("open", true);
      await panel.locator('.list-card').filter({hasText: "Manual short-term fact"}).locator('.actions .edit-temporary-memory').click();
      await panel.locator("#temporary-memory-expiry").fill("2026-10-03T19:00");
      await panel.locator("#temporary-memory-save").click();
      await expect(panel.locator("#temporary-memory-dialog")).not.toHaveJSProperty("open", true);
      expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_update").at(-1).expires_at)).toBe("2026-10-03T18:00:00.000Z");
      await expectHarnessClean(page, errors);
    });
  }

  test(`Type switching retains the draft and protects dismissal (${bundled})`, async ({page}) => {
    const panel = await openDataCollection(page, "temporary", 20, bundled);
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill("Long-term draft");
    await panel.locator("#memory-type").selectOption("persistent");
    await panel.locator("#memory-content").press("Escape");
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#confirm-cancel").click();
    await expect(panel.locator("#memory-content")).toHaveValue("Long-term draft");
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open", true);
    await panel.locator('.memory-kind[data-kind="persistent"]').click();
    await expect(panel.locator(".memory-list")).toContainText("Long-term draft");
    await panel.locator('.list-card').filter({hasText: "Long-term draft"}).locator('.memory-edit-button').click();
    await expect(panel.locator("#memory-type")).toHaveValue("persistent");
    await expect(panel.locator("#memory-type")).toBeDisabled();
  });

  test(`Conversation and Backup headings precede their cards (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("data-memory/conversations", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.getByRole("heading", {name: "Conversation history", exact: true})).toBeVisible();
    const order = await panel.evaluate(host => {
      const root = host.shadowRoot;
      const selectors = ['h1', '[aria-label="Conversation scope"]', '[data-eoc-active-conversations]', '[data-eoc-history-config]'];
      const nodes = selectors.map(selector => root.querySelector(selector));
      return nodes.every(Boolean) && nodes.every((node, index) => !index || Boolean(nodes[index - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING));
    });
    expect(order).toBe(true);
    await page.goto(fixtureUrl("usage-maintenance/backup-restore", bundled ? "&bundle=1" : ""));
    await expect(panel.locator(".page-intro")).toContainText("Back up, restore, or move your assistant setup and saved data.");
    expect(await panel.evaluate(host => {
      const title = host.shadowRoot.querySelector('.page-intro'), card = host.shadowRoot.querySelector('.backup-surface');
      return !card.contains(title) && Boolean(title.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING);
    })).toBe(true);
    await expect(panel.locator(".backup-surface")).toContainText("Export / Backup");
    await expect(panel.locator(".backup-surface")).toContainText("Import / Restore");
  });

  test(`Functions Add menu supports keyboard, dismissal and all actions (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    const trigger = panel.locator("#function-add");
    await expect(trigger).toBeVisible();
    await expect(panel.locator("#add-tool")).toBeHidden();
    await trigger.focus();
    await trigger.press("ArrowDown");
    await expect(panel.locator("#add-tool")).toBeFocused();
    await panel.locator("#add-tool").press("End");
    await expect(panel.locator("#add-group")).toBeFocused();
    await panel.locator("#add-group").press("Escape");
    await expect(trigger).toBeFocused();
    await expect(trigger).toHaveAttribute("aria-expanded", "false");
    await trigger.click();
    await panel.locator("#add-group").click();
    await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#group-cancel").click();
    await trigger.click();
    await panel.locator("#add-tool").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#tool-cancel").click();
    await trigger.click();
    await panel.locator("#add-ha-tools").click();
    await expect(panel.locator("dialog[data-ha-llm-tools-dialog]")).toHaveJSProperty("open", true);
    await expect(panel.getByRole("heading", {name: "Add Home Assistant tools"})).toBeVisible();
  });

  test(`Usage explains daily history and saved runs in plain language (${bundled})`, async ({page}) => {
    await page.goto(fixtureUrl("usage-maintenance/usage", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".usage-history-note")).toContainText("Daily usage history:");
    await expect(panel.locator(".usage-history-note")).toContainText("Older daily history is loaded only when you choose a longer history range.");
    await expect(panel.locator("main")).toContainText("This table shows saved individual runs.");
    await expect(panel.locator("main")).toContainText("Usage totals are kept even when detailed records are deleted.");
    await expect(panel.locator("main")).not.toContainText("loaded window");
  });
}

test("Functions Add menu fits a narrow screen and closes on an outside click", async ({page}) => {
  await page.setViewportSize({width: 390, height: 700});
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  const bounds = await panel.locator("#function-add-menu").boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(700);
  await panel.getByRole("heading", {name: "Function Tools & Groups", exact: true}).click();
  await expect(panel.locator("#function-add-menu")).toBeHidden();
});
