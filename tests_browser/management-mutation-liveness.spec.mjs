import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

async function holdFirstMutation(page, section, action) {
  await page.evaluate(({section, action}) => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    window.browserHarness.livenessMutation = {
      section,
      action,
      calls: 0,
      started: false,
      release,
    };
    hass.callWS = async (message) => {
      if (message.section === section && message.action === action) {
        window.browserHarness.livenessMutation.calls += 1;
        if (window.browserHarness.livenessMutation.calls === 1) {
          window.browserHarness.livenessMutation.started = true;
          await gate;
        }
      }
      return original(message);
    };
  }, {section, action});
}

async function waitForHeldMutation(page) {
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation?.started)).toBe(true);
}

async function releaseHeldMutation(page) {
  await page.evaluate(() => window.browserHarness.livenessMutation.release());
}

async function expectOverviewLive(panel) {
  await panel.evaluate((host) => host._navigate("overview"));
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await expect(panel.locator("main")).not.toHaveAttribute("aria-busy", "true");
}

test("configuration remains live for an immediate second save and route read", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const maxTokens = panel.locator('[data-config="max_tokens"]');
  await expect(maxTokens).toBeVisible();

  await holdFirstMutation(page, "configuration", "save");
  await maxTokens.fill("760");
  await panel.locator("#save-config").click();
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect.poll(() => panel.evaluate((host) => host._configDirty)).toBe(false);

  await maxTokens.fill("761");
  await expect(panel.locator("#save-config")).toBeVisible();
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation.calls)).toBe(2);
  await expect.poll(() => panel.evaluate((host) => host._configDirty)).toBe(false);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("a failed configuration save does not poison the next mutation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const maxTokens = panel.locator('[data-config="max_tokens"]');
  await expect(maxTokens).toBeVisible();

  await page.evaluate(() => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    window.browserHarness.failedLivenessSaveCalls = 0;
    hass.callWS = async (message) => {
      if (message.section === "configuration" && message.action === "save") {
        window.browserHarness.failedLivenessSaveCalls += 1;
        if (window.browserHarness.failedLivenessSaveCalls === 1) throw new Error("Injected save failure");
      }
      return original(message);
    };
  });

  await maxTokens.fill("762");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.failedLivenessSaveCalls)).toBe(1);
  await expect(panel.locator("#save-config")).toBeEnabled();

  await maxTokens.fill("763");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.failedLivenessSaveCalls)).toBe(2);
  await expect.poll(() => panel.evaluate((host) => host._configDirty)).toBe(false);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("Knowledge update completion leaves the collection immediately mutable and navigable", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "knowledge", 3);
  await holdFirstMutation(page, "knowledge", "update");

  await panel.locator('[data-source-id="source-0"] .source-edit-button').click();
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#knowledge-description").fill("First liveness update");
  await panel.locator("#knowledge-save").click();
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);

  await panel.locator('[data-source-id="source-1"] .source-edit-button').click();
  await panel.locator("#knowledge-description").fill("Second liveness update");
  await panel.locator("#knowledge-save").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation.calls)).toBe(2);
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("persistent Memory update completion leaves the next memory immediately mutable", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "persistent", 3);
  await holdFirstMutation(page, "memories", "update");

  await panel.locator('[data-memory-id="memory-0"] .memory-edit-button').click();
  await panel.locator("#memory-content").fill("Memory zero liveness update");
  await panel.locator("#memory-save").click();
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);

  await panel.locator('[data-memory-id="memory-1"] .memory-edit-button').click();
  await panel.locator("#memory-content").fill("Memory one liveness update");
  await panel.locator("#memory-save").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation.calls)).toBe(2);
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("Temporary Memory delete completion does not block the next destructive mutation", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "temporary", 12);
  await holdFirstMutation(page, "memories", "temporary_delete");

  await panel.locator('[data-memory-id="temporary-0"] .delete-temporary').click();
  await acceptConfirmation(panel);
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect(panel.locator('[data-memory-id="temporary-0"]')).toHaveCount(0);

  await panel.locator('[data-memory-id="temporary-1"] .delete-temporary').click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation.calls)).toBe(2);
  await expect(panel.locator('[data-memory-id="temporary-1"]')).toHaveCount(0);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("Request Rule deletion releases the surface for an immediate update and navigation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".request-rule-card")).toHaveCount(1);

  await panel.locator(".request-rule-card .rule-duplicate").click();
  await expect(panel.locator(".request-rule-card")).toHaveCount(2);

  await holdFirstMutation(page, "request_rules", "delete");
  await panel.locator(".request-rule-card").nth(1).locator(".rule-delete").click();
  await acceptConfirmation(panel);
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect(panel.locator(".request-rule-card")).toHaveCount(1);

  const updatesBefore = await page.evaluate(() => window.browserHarness.calls.filter(
    (call) => call.section === "request_rules" && call.action === "update",
  ).length);
  const enabled = panel.locator(".request-rule-card .rule-enabled");
  if (await enabled.isChecked()) await enabled.uncheck();
  else await enabled.check();
  await expect.poll(() => page.evaluate((before) => window.browserHarness.calls.filter(
    (call) => call.section === "request_rules" && call.action === "update",
  ).length, updatesBefore)).toBe(updatesBefore + 1);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});

test("Guest Mode can save again immediately after a completed policy mutation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  const toggle = panel.locator("#guest-controls-enabled");
  await toggle.evaluate((input) => { input.closest("details").open = true; });

  await holdFirstMutation(page, "guest_mode", "save_policy");
  await toggle.check();
  await panel.locator("#save-page").click();
  await waitForHeldMutation(page);

  await releaseHeldMutation(page);
  await expect(panel.locator("#save-page")).toHaveCount(0);

  await toggle.uncheck();
  await expect(panel.locator("#save-page")).toBeVisible();
  await panel.locator("#save-page").click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.livenessMutation.calls)).toBe(2);
  await expect(panel.locator("#save-page")).toHaveCount(0);

  await expectOverviewLive(panel);
  await expectHarnessClean(page, errors);
});
