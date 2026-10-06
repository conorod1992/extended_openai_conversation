import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const representativeRoutes = [
  "assistant/basics",
  "capabilities/request-rules",
  "capabilities/functions",
  "data-memory/memories",
  "data-memory/knowledge",
];

async function assertDialogFocusContained(page, panel, dialogSelector, opener, restoreTarget = opener) {
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = panel.locator(dialogSelector);
  await expect(dialog).toHaveJSProperty("open", true);
  for (let index = 0; index < 30; index++) {
    await page.keyboard.press(index % 7 === 6 ? "Shift+Tab" : "Tab");
    // Native modal tab order may visit browser chrome at the wrap boundary.
    // No background page control may receive focus; the next Tab must return.
    const browserBoundary = await panel.evaluate(host =>
      !host.shadowRoot.activeElement && document.activeElement === document.body);
    if (browserBoundary) await page.keyboard.press("Tab");
    const contained = await panel.evaluate((host, selector) => {
      const dialog = host.shadowRoot.querySelector(selector);
      let active = host.shadowRoot.activeElement;
      while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
      return Boolean(dialog && active && dialog.contains(active));
    }, dialogSelector);
    expect(contained, `${dialogSelector} leaked focus on cycle ${index}`).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(restoreTarget).toBeFocused();
}

test("forced colours keep representative management controls visible and operable", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.emulateMedia({forcedColors: "active"});
  expect(await page.evaluate(() => matchMedia("(forced-colors: active)").matches)).toBe(true);

  for (const route of representativeRoutes) {
    await page.goto(fixtureUrl(route));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel).toBeVisible();
    const controls = panel.locator("button:visible, input:visible, select:visible, textarea:visible, summary:visible");
    const count = await controls.count();
    expect(count, `${route} exposed no operable controls`).toBeGreaterThan(0);
    for (let index = 0; index < Math.min(count, 20); index++) {
      const control = controls.nth(index);
      const box = await control.boundingBox();
      expect(box, `${route} control ${index} has no forced-colour box`).not.toBeNull();
      expect(box.width).toBeGreaterThan(0);
      expect(box.height).toBeGreaterThan(0);
      if (!await control.isDisabled().catch(() => false)) {
        await control.focus();
        await expect(control).toBeFocused();
      }
    }
  }
  await expectHarnessClean(page, errors);
});

test("reduced motion does not block navigation, dialogs, or committed saves", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.emulateMedia({reducedMotion: "reduce"});
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);

  await page.goto(fixtureUrl("assistant/basics"));
  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await title.fill("Reduced motion saved assistant");
  await panel.locator("#save-config").click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator(".rule-close").first().click();
  await expect(panel.locator("#rule-dialog")).not.toBeVisible();

  await page.goto(fixtureUrl("assistant/basics"));
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Reduced motion saved assistant");
  await expectHarnessClean(page, errors);
});

test("dynamic success and validation failures expose screen-reader announcement semantics", async ({page}) => {
  const errors = trackPageErrors(page);

  await page.goto(fixtureUrl("assistant/basics"));
  let panel = page.locator("extended-openai-management-panel");
  const toast = panel.locator("#toast");
  await expect(toast).toHaveAttribute("role", "status");
  await expect(toast).toHaveAttribute("aria-live", "polite");
  await panel.locator('[data-config="__title"]').fill("Announcement contract assistant");
  await panel.locator("#save-config").click();
  await expect(toast).toContainText(/saved|changes/i);

  await page.goto(fixtureUrl("data-memory/knowledge"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Announcement source");
  await panel.locator("#knowledge-content").fill("Announcement content");
  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    const original = panel._hass.callWS.bind(panel._hass);
    let fail = true;
    panel._hass.callWS = message => {
      if (fail && message.section === "knowledge" && message.action === "create") {
        fail = false;
        return Promise.reject(new Error("Screen reader validation probe"));
      }
      return original(message);
    };
  });
  await panel.locator("#knowledge-save").click();
  const knowledgeError = panel.locator("#knowledge-error");
  await expect(knowledgeError).toHaveAttribute("role", "alert");
  await expect(knowledgeError).toContainText("Screen reader validation probe");

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const yaml = panel.locator("#tool-yaml");
  const toolError = panel.locator("#tool-error");
  await expect(yaml).toHaveAttribute("aria-describedby", "tool-error");
  await expect(toolError).toHaveAttribute("role", "status");
  await expect(toolError).toHaveAttribute("aria-live", "polite");
  await yaml.fill("spec:\n  description: missing name");
  await panel.locator("#tool-validate").click();
  await expect(toolError).not.toHaveText("");
  await expectHarnessClean(page, errors);
});

test("major modal editors contain keyboard focus and restore it to their opener", async ({page}) => {
  const errors = trackPageErrors(page);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");
  await assertDialogFocusContained(
    page,
    panel,
    "#rule-dialog",
    panel.getByRole("button", {name: "Create rule", exact: true}).first(),
  );

  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await assertDialogFocusContained(page, panel, "#memory-dialog", panel.locator("#add-memory"));

  await page.goto(fixtureUrl("data-memory/knowledge"));
  panel = page.locator("extended-openai-management-panel");
  await assertDialogFocusContained(page, panel, "#knowledge-dialog", panel.locator("#add-source"));

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  const addTool = panel.locator("#add-tool");
  await assertDialogFocusContained(page, panel, "#tool-dialog", addTool, panel.locator("#function-add"));

  await expectHarnessClean(page, errors);
});

test("mobile primary and card actions meet a 24 CSS pixel touch-target floor", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width: 390, height: 844});

  const cases = [
    ["capabilities/request-rules", "button:visible:not(.guide-topic-link), summary:visible"],
    ["data-memory/memories", "button:visible:not(.guide-topic-link)"],
    ["data-memory/knowledge", "button:visible:not(.guide-topic-link)"],
    ["capabilities/functions", "button:visible:not(.guide-topic-link), summary:visible"],
  ];

  for (const [route, selector] of cases) {
    await page.goto(fixtureUrl(route));
    const panel = page.locator("extended-openai-management-panel");
    const controls = panel.locator(selector);
    await expect(controls.first()).toBeVisible();
    const count = await controls.count();
    expect(count, `${route} has no touch controls`).toBeGreaterThan(0);
    for (let index = 0; index < count; index++) {
      const control = controls.nth(index);
      if (!await control.isVisible()) continue;
      const box = await control.boundingBox();
      expect(box, `${route} touch target ${index} has no box`).not.toBeNull();
      expect(box.width, `${route} touch target ${index} is too narrow`).toBeGreaterThanOrEqual(24);
      expect(box.height, `${route} touch target ${index} is too short`).toBeGreaterThanOrEqual(24);
    }
  }
  await expectHarnessClean(page, errors);
});
