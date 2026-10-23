import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const [pageName, section, ready] of [
  ["guide", null, "#guide-search"],
  ["usage-maintenance", "usage", "#usage-window"],
  ["usage-maintenance", "request-debug", "extended-openai-debug-panel"],
  ["usage-maintenance", "diagnostics", "#test-agent"],
  ["usage-maintenance", "retention", "#config-retention"],
  ["usage-maintenance", "backup-restore", "#transfer-export-mode"],
]) {
  test(`dirty configuration survives ${section || pageName} and returns without a configuration read`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics"));
    const panel = page.locator("extended-openai-management-panel");
    const draft = panel.locator('[data-config="__title"]');
    await expect(draft).toHaveValue("Jarvis");
    const reads = await page.evaluate(() => browserHarness.calls.filter(
      (call) => call.section === "configuration" && call.action === "get",
    ).length);
    await draft.fill("Preserved draft");
    if (section) {
      await panel.locator(`.top-nav button[data-page="${pageName}"]`).click();
      await panel.locator(`#local-section`).selectOption(section, {force: true});
    } else await panel.locator(`.top-nav button[data-page="${pageName}"]`).click();
    await expect(panel.locator(ready)).toBeVisible();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
    await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveClass(/eoc-has-unsaved/);
    await panel.locator('.top-nav button[data-page="assistant"]').click();
    await expect(draft).toHaveValue("Preserved draft");
    await expect(panel.locator("#dirty-state")).toBeVisible();
    expect(await page.evaluate(() => browserHarness.calls.filter(
      (call) => call.section === "configuration" && call.action === "get",
    ).length)).toBe(reads);
    await expectHarnessClean(page, errors);
  });
}

test("assistant ownership and browser unload still protect a dirty draft", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Owned draft");
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})))).toBe(true);
  await panel.evaluate((host) => {
    host._data.agents.push({...host._data.agents[0], subentry_id: "agent-2", title: "Second assistant"});
    host._render();
  });
  await panel.locator("#agent").selectOption("agent-2");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-cancel").click();
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Owned draft");
  await panel.locator("#agent").selectOption("agent-2");
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#agent")).toHaveValue("agent-2");
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})))).toBe(false);
  await expectHarnessClean(page, errors);
});

test("dirty retention projection joins the full agent draft without losing its change", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/retention"));
  const panel = page.locator("extended-openai-management-panel");
  const retention = panel.locator('[data-config="usage_request_retention_days"]');
  await expect(retention).toBeVisible();
  const original = await retention.inputValue();
  const alternative = original === "7" ? "14" : "7";
  await retention.selectOption(alternative);
  await panel.locator('.top-nav button[data-page="assistant"]').click();
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expect(panel.locator("#dirty-state")).toBeVisible();
  await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();
  await panel.locator("#local-section").selectOption("retention", {force: true});
  await expect(retention).toHaveValue(alternative);
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
  await expectHarnessClean(page, errors);
});


for (const legacyDraft of [false, true]) {
  test(`archive retention survives navigation with matching saved value (legacy draft: ${legacyDraft})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    // Seed only the fixture transport's persisted configuration, then mount the
    // actual route. Navigation, editing and save all use production handlers.
    await page.evaluate(() => {
      const state = browserHarness.getState();
      Object.assign(state.configuration.config, {archive_enabled:true, archive_retention_days:30});
      state.configuration.options.archive_retention_days = [7, 30, 365].map(value => ({value, label:`${value} days`}));
      localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
    });
    await page.goto(fixtureUrl("data-memory/conversations"));
    const retention = panel.locator('[data-config="archive_retention_days"]');
    await expect(retention).toHaveValue("30");
    await retention.selectOption("365");
    expect(await panel.evaluate(host => host._draft.archive_retention_days)).toBe(365);
    await panel.locator('.top-nav button[data-page="assistant"]').click();
    await expect(panel.locator('[data-config="__title"]')).toBeVisible();
    if (legacyDraft) await panel.evaluate(host => {host._draft.archive_retention_days = "365";});
    await panel.evaluate(host => host._navigate("data-memory", "conversations"));
    await expect(retention).toHaveValue("365");
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").length)).toBe(1);
    expect(await page.evaluate(() => browserHarness.calls.find(call => call.section === "configuration" && call.action === "save").config.archive_retention_days)).toBe(legacyDraft ? "365" : 365);
    await expect(retention).toHaveValue("365");
    await expectHarnessClean(page, errors);
  });
}
