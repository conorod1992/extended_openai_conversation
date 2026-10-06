import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the genuine HA large-configuration bridge");

const fixture = (route) => "/tests_browser/real-ha-fixture.html?route=" + encodeURIComponent(route) + "&backend=" + encodeURIComponent(backendUrl);

const routes = [
  "overview",
  "assistant/basics",
  "assistant/model-responses",
  "assistant/conversation",
  "assistant/prompt-context",
  "assistant/voice",
  "assistant/speech",
  "assistant/advanced",
  "capabilities/home-assistant",
  "capabilities/web-skills",
  "capabilities/functions",
  "capabilities/request-rules",
  "capabilities/quiet-hours",
  "capabilities/guest-mode",
  "data-memory/memory-settings",
  "data-memory/memories",
  "data-memory/knowledge",
  "data-memory/conversations",
  "usage-maintenance/usage",
  "usage-maintenance/backup-restore",
  "usage-maintenance/diagnostics",
  "usage-maintenance/retention",
];

test("large genuine configuration remains usable across all management surfaces", async ({page}, testInfo) => {
  test.setTimeout(180_000);
  const pageErrors = trackPageErrors(page);
  const evidence = [];

  for (const route of routes) {
    const started = Date.now();
    await page.goto(fixture(route));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel).toHaveCount(1);
    await expect.poll(
      () => panel.evaluate((host) => ({
        view: host._viewKey?.(),
        text: host.shadowRoot?.textContent?.trim().length || 0,
      })),
      {timeout: 20_000},
    ).toEqual(expect.objectContaining({view: route}));
    const rendered = await panel.evaluate((host) => ({
      view: host._viewKey?.(),
      textLength: host.shadowRoot?.textContent?.trim().length || 0,
    }));
    expect(rendered.textLength).toBeGreaterThan(80);
    evidence.push({route, elapsed_ms: Date.now() - started, text_length: rendered.textLength});
  }

  await page.goto(fixture("capabilities/functions"));
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".tool-card").first()).toBeVisible();
  expect(await panel.locator(".tool-card").count()).toBeGreaterThanOrEqual(40);

  await page.goto(fixture("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".request-rule-card").first()).toBeVisible();
  expect(await panel.locator(".request-rule-card").count()).toBeGreaterThanOrEqual(60);

  await page.goto(fixture("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".list-card").first()).toBeVisible();
  expect(await panel.locator(".list-card").count()).toBeGreaterThanOrEqual(20);

  await page.goto(fixture("data-memory/knowledge"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".list-card").first()).toBeVisible();
  expect(await panel.locator(".list-card").count()).toBeGreaterThanOrEqual(20);

  await expectHarnessClean(page, pageErrors);
  await testInfo.attach("all-surfaces-scale-evidence", {
    body: JSON.stringify(evidence, null, 2),
    contentType: "application/json",
  });
});
