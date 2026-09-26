import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

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

test("stale Function Tool and Group editors preserve the newer genuine HA revisions", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page);
  const errorsB = trackPageErrors(other);
  const trace = [];
  try {
    await page.goto(realFixtureUrl("capabilities/functions"));
    const panelA = page.locator("extended-openai-management-panel");
    await expect(panelA.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
    await panelA.locator("#add-tool").click();
    await panelA.locator("#tool-yaml").fill(browserToolYaml("Two-tab initial tool"));
    await panelA.locator("#tool-save").click();
    await expect(panelA.locator(".tool-card").filter({hasText: "browser_tool"})).toBeVisible();
    await other.goto(realFixtureUrl("capabilities/functions"));
    const panelB = other.locator("extended-openai-management-panel");
    const toolA = panelA.locator(".tool-card").filter({hasText: "browser_tool"});
    const toolB = panelB.locator(".tool-card").filter({hasText: "browser_tool"});
    await expect(toolB).toBeVisible();
    await toolA.locator(".edit-tool").click();
    await toolB.locator(".edit-tool").click();
    await panelA.locator("#tool-yaml").fill(browserToolYaml("A committed tool revision"));
    await panelA.locator("#tool-save").click();
    await expect(toolA).toContainText("A committed tool revision");
    await panelB.locator("#tool-yaml").fill(browserToolYaml("B stale tool revision"));
    await panelB.locator("#tool-save").click();
    await expect(panelB.locator("#tool-error")).toContainText("changed in another tab");
    trace.push("Tool same-object stale save rejected");

    await other.goto(realFixtureUrl("capabilities/functions"));
    await panelA.locator("#add-group").click();
    await panelA.locator("#group-name").fill("Two-tab group");
    await panelA.locator("#group-id").fill("two-tab-group");
    await panelA.locator("#group-description").fill("Initial group description");
    await panelA.locator('#group-functions input[value="browser_tool"]').check();
    await panelA.locator("#group-save").click();
    await page.goto(realFixtureUrl("capabilities/functions"));
    await other.goto(realFixtureUrl("capabilities/functions"));
    const groupA = panelA.locator('.function-group-card[data-group-id="two-tab-group"]');
    const groupB = panelB.locator('.function-group-card[data-group-id="two-tab-group"]');
    await expect(groupA).toBeVisible();
    await expect(groupB).toBeVisible();
    await groupA.locator("summary").click();
    await groupB.locator("summary").click();
    await groupA.locator(".edit-group").click();
    await groupB.locator(".edit-group").click();
    await panelA.locator("#group-description").fill("A committed group revision");
    await panelA.locator("#group-save").click();
    await expect(groupA).toContainText("A committed group revision");
    await panelB.locator("#group-description").fill("B stale group revision");
    await panelB.locator("#group-save").click();
    await expect(panelB.locator("#group-error")).toContainText("changed in another tab");
    trace.push("Group same-object stale save rejected");
    await other.goto(realFixtureUrl("capabilities/functions"));
    await expect(panelB.locator('.function-group-card[data-group-id="two-tab-group"]')).toContainText("A committed group revision");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every(item => item.startsWith("400 "))).toBe(true);
  } finally {
    await testInfo.attach("multi-tab-function-conflicts", {body: JSON.stringify({trace}, null, 2), contentType: "application/json"});
    await other.close();
  }
});

test("stale Knowledge edit cannot overwrite a newer genuine HA source", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page), errorsB = trackPageErrors(other);
  try {
    await page.goto(realFixtureUrl("data-memory/knowledge"));
    const panelA = page.locator("extended-openai-management-panel");
    await expect(panelA.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
    await panelA.locator("#add-source").click();
    await panelA.locator("#knowledge-title").fill("Two-tab Knowledge source");
    await panelA.locator("#knowledge-content").fill("Original Knowledge content");
    await panelA.locator("#knowledge-save").click();
    await expect(panelA.locator(".list-card").filter({hasText: "Two-tab Knowledge source"})).toBeVisible();
    await other.goto(realFixtureUrl("data-memory/knowledge"));
    const panelB = other.locator("extended-openai-management-panel");
    await panelA.locator(".list-card").filter({hasText: "Two-tab Knowledge source"}).locator(".source-edit-button").click();
    await panelB.locator(".list-card").filter({hasText: "Two-tab Knowledge source"}).locator(".source-edit-button").click();
    await expect(panelA.locator("#knowledge-content")).toHaveValue("Original Knowledge content");
    await expect(panelB.locator("#knowledge-content")).toHaveValue("Original Knowledge content");
    await panelA.locator("#knowledge-content").fill("A committed Knowledge content");
    await panelA.locator("#knowledge-save").click();
    await expect(panelA.locator("#knowledge-dialog")).not.toBeVisible();
    await panelB.locator("#knowledge-content").fill("B stale Knowledge content");
    await panelB.locator("#knowledge-save").click();
    await expect(panelB.locator("#knowledge-error")).toContainText("changed in another tab");
    await expect(panelB.locator("#knowledge-content")).toHaveValue("B stale Knowledge content");
    await other.goto(realFixtureUrl("data-memory/knowledge"));
    await panelB.locator(".list-card").filter({hasText: "Two-tab Knowledge source"}).locator(".source-edit-button").click();
    await expect(panelB.locator("#knowledge-content")).toHaveValue("A committed Knowledge content");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every(item => item.startsWith("400 "))).toBe(true);
  } finally {
    await testInfo.attach("multi-tab-knowledge-conflict", {body: JSON.stringify({surface: "Knowledge", conflicts: 1}), contentType: "application/json"});
    await other.close();
  }
});

test("backgrounded Memory tab rejects stale edits and delayed reads after a newer HA save", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page), errorsB = trackPageErrors(other);
  const trace = [];
  try {
    await page.goto(realFixtureUrl("data-memory/memories"));
    const panelA = page.locator("extended-openai-management-panel");
    await expect(panelA.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
    await panelA.locator("#add-memory").click();
    await panelA.locator("#memory-content").fill("Two-tab Memory original");
    await panelA.locator("#memory-save").click();
    await expect(panelA.getByText("Two-tab Memory original", {exact: true})).toBeVisible();
    await other.goto(realFixtureUrl("data-memory/memories"));
    const panelB = other.locator("extended-openai-management-panel");
    await expect(panelB.getByText("Two-tab Memory original", {exact: true})).toBeVisible();

    await panelA.locator(".list-card").filter({hasText: "Two-tab Memory original"}).locator(".memory-edit-button").click();
    await panelB.locator(".list-card").filter({hasText: "Two-tab Memory original"}).locator(".memory-edit-button").click();
    await panelA.locator("#memory-content").fill("Two-tab Memory authoritative");
    await panelA.locator("#memory-save").click();
    await expect(panelA.getByText("Two-tab Memory authoritative", {exact: true})).toBeVisible();
    await panelB.locator("#memory-content").fill("Two-tab Memory stale draft");
    await panelB.locator("#memory-save").click();
    await expect(panelB.locator("#memory-error")).toContainText("changed in another tab");
    await expect(panelB.locator("#memory-content")).toHaveValue("Two-tab Memory stale draft");
    trace.push("same-object Memory save rejected and draft preserved");

    await other.goto(realFixtureUrl("data-memory/memories"));
    await expect(panelB.getByText("Two-tab Memory authoritative", {exact: true})).toBeVisible();
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS;
      browserHarness.hass.callWS = async (message) => {
        const response = await original(message);
        if (message.section === "memories" && message.action === "list" && !window.__staleCaptured) {
          window.__staleCaptured = true;
          return new Promise((resolve) => { window.__releaseStale = () => resolve(response); });
        }
        return response;
      };
      browserHarness.panel._sectionCache.clear();
      void browserHarness.panel._loadSection();
    });
    await expect.poll(() => page.evaluate(() => Boolean(window.__staleCaptured))).toBe(true);
    trace.push("old genuine HA read held while tab inactive");

    await other.bringToFront();
    await panelB.locator(".list-card").filter({hasText: "Two-tab Memory authoritative"}).locator(".memory-edit-button").click();
    await panelB.locator("#memory-content").fill("Two-tab Memory newer backend");
    await panelB.locator("#memory-save").click();
    await expect(panelB.getByText("Two-tab Memory newer backend", {exact: true})).toBeVisible();
    trace.push("foreground tab committed newer backend state");

    await page.bringToFront();
    await page.evaluate(() => {
      browserHarness.panel._sectionCache.clear();
      void browserHarness.panel._loadSection();
    });
    await expect(panelA.getByText("Two-tab Memory newer backend", {exact: true})).toBeVisible();
    await page.evaluate(() => window.__releaseStale());
    await expect(panelA.getByText("Two-tab Memory newer backend", {exact: true})).toBeVisible();
    await expect(panelA.getByText("Two-tab Memory authoritative", {exact: true})).toHaveCount(0);
    trace.push("released stale read did not overwrite current HA Memory");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every(item => item.startsWith("400 "))).toBe(true);
  } finally {
    await testInfo.attach("multi-tab-memory-recovery", {body: JSON.stringify({layer: "browser + real-ha", trace}), contentType: "application/json"});
    await other.close();
  }
});

test("stale Guest policy cannot weaken a newer genuine HA policy", async ({page, context}, testInfo) => {
  test.setTimeout(120_000);
  const other = await context.newPage();
  const errorsA = trackPageErrors(page), errorsB = trackPageErrors(other);
  try {
    await page.goto(realFixtureUrl("capabilities/guest-mode"));
    const panelA = page.locator("extended-openai-management-panel");
    const reviewLegacy = panelA.locator("#guest-review-converted");
    await reviewLegacy.click();
    const knowledgeA = panelA.locator('[data-guest-mode="guest_knowledge_policy"]');
    await expect(knowledgeA).toBeVisible();
    await knowledgeA.selectOption("on");
    await panelA.locator("#save-page").click();
    await expect(panelA.locator(".save-bar")).toHaveCount(0);
    await other.goto(realFixtureUrl("capabilities/guest-mode"));
    const panelB = other.locator("extended-openai-management-panel");
    const knowledgeB = panelB.locator('[data-guest-mode="guest_knowledge_policy"]');
    await expect(knowledgeB).toHaveValue("on");
    await knowledgeA.selectOption("off");
    await panelA.locator("#save-page").click();
    await expect(panelA.locator(".save-bar")).toHaveCount(0);
    await knowledgeB.selectOption("custom");
    await panelB.locator("#save-page").click();
    await expect(panelB.locator("#toast")).toContainText("changed in another tab");
    await expect(knowledgeB).toHaveValue("custom");
    await other.goto(realFixtureUrl("capabilities/guest-mode"));
    await expect(panelB.locator('[data-guest-mode="guest_knowledge_policy"]')).toHaveValue("off");
    await expectHarnessClean(page, errorsA);
    expect(errorsB).toHaveLength(0);
    expect(errorsB.badResponses.every(item => item.startsWith("400 "))).toBe(true);
  } finally {
    await testInfo.attach("multi-tab-guest-conflict", {body: JSON.stringify({surface: "Guest Mode", conflicts: 1}), contentType: "application/json"});
    await other.close();
  }
});
