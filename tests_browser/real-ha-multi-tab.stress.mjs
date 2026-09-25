import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
test.skip(!backendUrl, "requires the genuine Home Assistant management bridge");
const realFixtureUrl = (route) => `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backendUrl)}`;

test("a stale Request Rule editor in another tab cannot overwrite a newer save", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page);
  const errorsB = trackPageErrors(other);
  const trace = [];
  try {
    await page.goto(realFixtureUrl("capabilities/request-rules"));
    const panelA = page.locator("extended-openai-management-panel");
    await expect(panelA.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
    await panelA.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await panelA.locator("#rule-name").fill("Two-tab protected rule");
    await panelA.locator("#rule-phrases").fill("two tab conflict phrase");
    await panelA.locator("#rule-action-type").selectOption("model_routing");
    await panelA.locator("#rule-model").fill("gpt-5-mini");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "Two-tab protected rule", exact: true})).toBeVisible();
    trace.push("A created rule");

    await other.goto(realFixtureUrl("capabilities/request-rules"));
    const panelB = other.locator("extended-openai-management-panel");
    await expect(panelB.getByRole("heading", {name: "Two-tab protected rule", exact: true})).toBeVisible();
    await panelA.locator(".request-rule-card").filter({hasText: "Two-tab protected rule"}).locator(".rule-edit").click();
    await panelB.locator(".request-rule-card").filter({hasText: "Two-tab protected rule"}).locator(".rule-edit").click();
    await expect(panelA.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panelB.locator("#rule-dialog")).toHaveJSProperty("open", true);
    trace.push("A and B opened same revision");

    await panelA.locator("#rule-name").fill("A's committed change");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "A's committed change", exact: true})).toBeVisible();
    trace.push("A committed new revision");

    await panelB.locator("#rule-name").fill("B's stale change");
    await panelB.locator("#rule-save").click();
    await expect(panelB.getByText(/changed in another tab/i).first()).toBeVisible();
    await expect(panelB.locator("#rule-name")).toHaveValue("B's stale change");
    trace.push("B's stale save rejected and draft preserved");

    await other.goto(realFixtureUrl("capabilities/request-rules"));
    await expect(panelB.getByRole("heading", {name: "A's committed change", exact: true})).toBeVisible();
    await expect(panelB.getByRole("heading", {name: "B's stale change", exact: true})).toHaveCount(0);
    trace.push("B reloaded authoritative A revision");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every((item) => item.startsWith("400 "))).toBe(true);
    expect(await other.evaluate(() => ({errors: window.browserHarness.windowErrors, rejections: window.browserHarness.rejections}))).toEqual({errors: [], rejections: []});
  } finally {
    await testInfo.attach("multi-tab-operations", {body: JSON.stringify({trace}, null, 2), contentType: "application/json"});
    await other.close();
  }
});

test("a staged Assistant edit cannot overwrite a genuine HA backup restore", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page);
  const errorsB = trackPageErrors(other);
  const trace = [];
  try {
    await page.goto(realFixtureUrl("assistant/basics"));
    await other.goto(realFixtureUrl("assistant/basics"));
    const panelA = page.locator("extended-openai-management-panel");
    const panelB = other.locator("extended-openai-management-panel");
    const titleA = panelA.locator('[data-config="__title"]');
    const titleB = panelB.locator('[data-config="__title"]');
    await expect(titleA).toBeVisible();
    await expect(titleB).toBeVisible();
    await titleB.fill("Stale tab B title");
    trace.push("B staged Assistant configuration from old revision");

    const restoredTitle = "Restored authoritative Assistant";
    await page.evaluate(async title => {
      const call = browserHarness.calls.find(item => item.section === "configuration" && item.action === "get");
      if (!call) throw new Error("Configuration request identity unavailable");
      const base = {
        type: "extended_openai_conversation_responses/management",
        entry_id: call.entry_id,
        subentry_id: call.subentry_id,
        section: "backup",
      };
      const exported = await browserHarness.hass.callWS({...base, action: "create"});
      const document = JSON.parse(exported.json);
      document.agent.title = title;
      await browserHarness.hass.callWS({...base, action: "restore", document, confirm: true});
    }, restoredTitle);
    trace.push("A restored a backup with different authoritative title");

    await panelB.locator("#save-config").click();
    await expect(panelB.locator("#toast")).toContainText("changed in another tab");
    await expect(titleB).toHaveValue("Stale tab B title");
    trace.push("B stale save rejected with draft preserved");

    await other.goto(realFixtureUrl("assistant/basics"));
    await expect(panelB.locator('[data-config="__title"]')).toHaveValue(restoredTitle);
    trace.push("B reload read the restored authoritative title");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every(item => item.startsWith("400 "))).toBe(true);
    expect(await other.evaluate(() => ({errors: browserHarness.windowErrors, rejections: browserHarness.rejections}))).toEqual({errors: [], rejections: []});
  } finally {
    await testInfo.attach("multi-tab-backup-restore", {body: JSON.stringify({trace}, null, 2), contentType: "application/json"});
    await other.close();
  }
});
