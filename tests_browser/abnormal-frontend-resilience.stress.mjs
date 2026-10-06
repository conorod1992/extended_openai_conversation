import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("malformed management response becomes a recoverable route error and retry succeeds", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let malformed = true;
    hass.callWS = async message => {
      if (malformed && message.section === "configuration" && message.action === "get") {
        malformed = false;
        return {revision: "malformed-without-config"};
      }
      return original(message);
    };
  });

  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await expect(panel.locator(".route-error-state")).toBeVisible();
  await expect(panel.locator(".route-error-state")).toContainText("Unable to load this section");
  await expect(panel.locator("#retry-route")).toBeVisible();

  await panel.locator("#retry-route").click();
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await expect(panel.locator(".route-error-state")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});

test("IME composition interrupted by blur preserves one exact value without duplicate mutation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-source").click();
  const title = panel.locator("#knowledge-title");
  const content = panel.locator("#knowledge-content");

  await title.focus();
  await title.evaluate(element => {
    element.dispatchEvent(new CompositionEvent("compositionstart", {data: ""}));
    element.value = "途中入力";
    element.dispatchEvent(new InputEvent("input", {
      data: "途中入力",
      inputType: "insertCompositionText",
      bubbles: true,
      composed: true,
      isComposing: true,
    }));
  });
  // Interrupt composition by moving focus away without a compositionend event.
  await content.focus();
  await content.fill("IME interruption content");
  await expect(title).toHaveValue("途中入力");

  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
  await expect(panel.locator(".list-card").filter({hasText: "途中入力"})).toHaveCount(1);

  const creates = await page.evaluate(() =>
    browserHarness.calls.filter(call => call.section === "knowledge" && call.action === "create")
  );
  expect(creates).toHaveLength(1);
  expect(creates[0].title).toBe("途中入力");
  await expectHarnessClean(page, errors);
});

test("complete management stylesheet failure leaves the critical shell navigable and editable", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.route(/\/management(?:-[^/]+)?\.css$/, route => route.abort("failed"));
  await page.goto(fixtureUrl("assistant/basics"), {waitUntil: "domcontentloaded"});

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("style[data-eoc-critical-styles]")).toHaveCount(1);
  await expect(panel.locator(".page-heading .product-title")).toHaveText("Extended OpenAI");
  await expect(panel.getByRole("heading", {name: "Assistant settings", exact: true})).toBeVisible();
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible();
  await title.fill("Usable without full stylesheet");
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  expect(errors).toHaveLength(0);
  // Chromium emits a resource diagnostic for the stylesheet deliberately aborted above.
  expect(errors.consoleErrors.every(item => /^Failed to load resource: net::ERR_FAILED \(.*\/management(?:-[^/]+)?\.css:0\)$/.test(item))).toBe(true);
  expect(errors.badResponses).toEqual([]);
  expect(errors.requestFailures.length).toBeGreaterThanOrEqual(1);
  expect(errors.requestFailures.every(item => /management(?:-[^/]+)?\.css/.test(item))).toBe(true);
  expect(await page.evaluate(() => ({
    errors: browserHarness.windowErrors,
    rejections: browserHarness.rejections,
  }))).toEqual({errors: [], rejections: []});
});

test("very slow mutation protects its editor and resumes navigation after one save", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));
  let panel = page.locator("extended-openai-management-panel");
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Slow isolated mutation");
  await panel.locator("#memory-category").fill("slow-isolation");

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let release;
    window.__slowIsolation = {started: false, calls: 0, release: () => release?.()};
    hass.callWS = async message => {
      if (message.section === "memories" && message.action === "add" && !window.__slowIsolation.started) {
        window.__slowIsolation.started = true;
        window.__slowIsolation.calls++;
        await new Promise(resolve => { release = resolve; });
      }
      return original(message);
    };
  });

  await panel.locator("#memory-save").click();
  await expect.poll(() => page.evaluate(() => window.__slowIsolation.started)).toBe(true);
  await expect(panel.locator("#memory-save")).toBeDisabled();

  // Leaving an editor during an in-flight save is deliberately guarded.
  await panel.evaluate(host => host._navigate("guide"));
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#memory-content")).toHaveValue("Slow isolated mutation");
  await expect(page).toHaveURL(/\/extended-openai\/data-memory\/memories$/);
  await expect(panel.locator("#memory-save")).toBeDisabled();

  await page.evaluate(() => window.__slowIsolation.release());
  await expect(panel.locator("#memory-dialog")).not.toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__slowIsolation.calls)).toBe(1);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "memories" && call.action === "add").length)).toBe(1);
  await panel.evaluate(host => host._navigate("guide"));
  await expect(panel.getByRole("heading", {name: "Guide", exact: true})).toBeVisible();
  await expect(panel.locator("#guide-search")).toBeVisible();

  await panel.evaluate(host => host._navigate("data-memory", "memories"));
  await expect(panel.getByText("Slow isolated mutation", {exact: true})).toHaveCount(1);
  await expectHarnessClean(page, errors);
});
