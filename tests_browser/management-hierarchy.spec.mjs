import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

test("subsection navigation keeps its description accessible without a desktop tagline", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await expect(panel.locator(".section-selector p")).toHaveCount(0);
  await expect(panel.locator("#local-section")).toHaveAttribute("aria-description", /local commands/);
  await expect(panel.locator(".section-selector")).toBeHidden();
  await page.setViewportSize({width: 390, height: 780});
  await expect(panel.locator("#local-section")).toBeVisible();
  await panel.locator("#local-section").selectOption("functions");
  await expect(page).toHaveURL(/\/capabilities\/functions$/);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups"})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("guide links belong to section introductions and open the matching topic", async ({page}) => {
  const errors = trackPageErrors(page);
  const cases = [
    ["data-memory/memory-settings", "memory", "Memory settings"],
    ["data-memory/memories", "memory", "Memories"],
    ["data-memory/knowledge", "knowledge", "Knowledge Library"],
    ["capabilities/functions", "functions", "Function Tools & Groups"],
  ];
  const panel = page.locator("extended-openai-management-panel");
  for (const [route, topic, heading] of cases) {
    await page.goto(fixtureUrl(route));
    await expect(panel.getByRole("heading", {name: heading, exact: true})).toBeVisible();
    const link = panel.locator(`.page-intro .guide-topic-link[data-guide-topic="${topic}"], .section-heading .guide-topic-link[data-guide-topic="${topic}"]`);
    await expect(link).toBeVisible();
    await expect(link).toHaveText("Learn more");
    await link.click();
    await expect(page).toHaveURL(/\/extended-openai\/guide$/);
    await expect(panel.locator(`#guide-${topic}`)).toHaveJSProperty("open", true);
  }
  await expectHarnessClean(page, errors);
});

test("Request Rules keeps one create path when empty and the in-place toolbar when populated", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#rule-search")).toBeVisible();
  await expect(panel.locator(".rule-toolbar .count")).toHaveText("1 rule");
  await expect(panel.locator("#rule-add")).toBeVisible();
  await panel.locator("#rule-search").fill("no match");
  await expect(panel.locator("[data-eoc-rule-search-empty]")).toBeVisible();
  await expect(panel.locator("#rule-search")).toBeFocused();
  await panel.locator("#rule-search").fill("");
  await expect(panel.locator('[data-rule-key="rule-1"]')).toBeVisible();
  await panel.locator(".rule-settings summary").first().click();
  await expect(panel.locator("#rules-default-word-forms")).toBeVisible();
  await panel.locator(".rule-settings summary").last().click();
  await expect(panel.locator("#wording-add")).toBeVisible();

  await page.evaluate(() => {
    const state = browserHarness.getState();
    state.requestRules.rules = [];
    localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
  });
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await expect(panel.getByRole("heading", {name: "Create your first Request Rule"})).toBeVisible();
  await expect(panel.locator("#rule-search")).toBeHidden();
  await expect(panel.getByRole("button", {name: "Create rule", exact: true})).toHaveCount(1);
  await panel.locator("#rule-empty-add").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("First browser rule");
  await panel.locator("#rule-phrases").fill("hello browser");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator("#rule-search")).toBeVisible();
  await expect(panel.locator(".rule-toolbar .count")).toHaveText("1 rule");
  await expect(panel.locator("#rule-add")).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("rule testing distinguishes safe preview from confirmed live execution", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Test rules"})).toBeVisible();
  await expect(panel.locator("#rule-match-tester .notice")).toHaveCount(0);
  await expect(panel.locator("#rule-match-test-text")).toHaveAttribute("maxlength", "2048");
  await panel.locator("#rule-match-test-text").fill("baseline route");
  await panel.locator("#rule-match-test").click();
  await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "test_match").length)).toBeGreaterThan(0);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "test").length)).toBe(0);
  const live = panel.locator("#eoc-rule-live-test");
  await expect(live).not.toHaveJSProperty("open", true);
  await live.locator("summary").click();
  await expect(live.locator(".eoc-live-label")).toHaveText("Live");
  await live.locator("#eoc-rule-live-text").fill("baseline route");
  await live.locator("#eoc-rule-live-run").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-cancel").click();
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "test").length)).toBe(0);
  await expectHarnessClean(page, errors);
});

test("Conversation history keeps scope selection and retained search under clear headings", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/conversations"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Conversation history"})).toBeVisible();
  await expect(panel.locator('.scope-bar[aria-label="Conversation scope"]')).toBeVisible();
  await expect(panel.getByRole("heading", {name: "Retained conversations"})).toBeVisible();
  await panel.locator("#archive-query").fill("kitchen");
  await panel.locator("#archive-search").click();
  await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(call => call.section === "conversations" && call.action === "search").length)).toBe(1);
  await expectHarnessClean(page, errors);
});
