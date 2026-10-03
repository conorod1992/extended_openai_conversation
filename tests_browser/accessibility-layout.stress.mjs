import {expect, test} from "@playwright/test";
import {mkdirSync, writeFileSync} from "node:fs";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const pages = [
  ["assistant/basics", "General"],
  ["capabilities/request-rules", "Request Rules"],
  ["capabilities/functions", "Function Tools & Groups"],
  ["capabilities/guest-mode", "Guest Mode"],
  ["capabilities/quiet-hours", "Quiet Hours"],
  ["data-memory/memories", "Memories"],
  ["data-memory/knowledge", "Sources"],
];

test("major management pages keep unique IDs and reachable navigation at narrow and wide sizes", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  let checked = 0;
  for (const width of [390, 1600]) {
    await page.setViewportSize({width, height: 850});
    for (const [route, heading] of pages) {
      await page.goto(fixtureUrl(route));
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel.getByRole("heading", {name: heading, exact: true}).first()).toBeVisible();
      const duplicateIds = await panel.evaluate(host => {
        const ids = [...host.shadowRoot.querySelectorAll("[id]")].map(node => node.id);
        return ids.filter((id, index) => ids.indexOf(id) !== index);
      });
      expect(duplicateIds, `${route} at ${width}px`).toEqual([]);
      await expect(panel.locator(width < 600 ? "#local-section" : ".top-nav")).toBeVisible();
      await expect(panel).toHaveCount(1);
      checked++;
    }
  }
  await expectHarnessClean(page, errors);
  await testInfo.attach("layout-pages", {body: JSON.stringify({pages: checked, widths: [390, 1600]}), contentType: "application/json"});
  const directory = process.env.STRESS_ARTIFACT_DIR || "stress-artifacts";
  mkdirSync(directory, {recursive: true});
  writeFileSync(`${directory}/browser-accessibility.json`, JSON.stringify({accessibilityLayoutPages: checked, widths: [390, 1600]}));
  console.log(`ENHANCED ACCESSIBILITY_LAYOUT pages=${checked}`);
});

test("Request Rule dialog supports keyboard cancellation and returns usable focus", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  const create = panel.getByRole("button", {name: "Create rule", exact: true}).first();
  await create.focus();
  await page.keyboard.press("Enter");
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#rule-name")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  await expect(create).toBeFocused();
  await expectHarnessClean(page, errors);
});

test("large text and long names keep rule edit actions reachable", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width: 390, height: 800});
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const edit = panel.locator(".request-rule-card .rule-edit").first();
  await expect(edit).toBeVisible();
  await edit.scrollIntoViewIfNeeded();
  await edit.click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("Long rule name ".repeat(30));
  await expect(panel.locator("#rule-save")).toBeVisible();
  await panel.locator("#rule-save").scrollIntoViewIfNeeded();
  await expect(panel.locator("#rule-save")).toBeEnabled();
  await expectHarnessClean(page, errors);
});

test("390px save bars and validation dialogs complete critical writes without page overflow", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width:390, height:780});
  const overflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Narrow saved assistant");
  const save = panel.locator("#save-config");
  await save.scrollIntoViewIfNeeded();
  await expect(save).toBeInViewport();
  await overflow();
  await save.click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Narrow saved assistant");

  await page.goto(fixtureUrl("data-memory/knowledge"));
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Narrow source ".repeat(12));
  await panel.locator("#knowledge-content").fill("Narrow persisted context");
  await page.evaluate(() => {
    const harness = window.browserHarness, original = harness.hass.callWS.bind(harness.hass);
    let fail = true;
    harness.hass.callWS = message => {
      if (message.section === "knowledge" && message.action === "create" && fail) {
        fail = false;
        return Promise.reject(new Error("Validation recovery: ".repeat(15)));
      }
      return original(message);
    };
  });
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-error")).toContainText("Validation recovery");
  const retry = panel.locator("#knowledge-save");
  await retry.scrollIntoViewIfNeeded();
  await expect(retry).toBeInViewport();
  await expect(retry).toBeEnabled();
  await overflow();
  await retry.click();
  await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
  await page.goto(fixtureUrl("data-memory/knowledge"));
  await expect(panel.locator("[data-source-id]")).toContainText("Narrow source");
  await overflow();
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  await expect(panel.locator('input[type="file"]')).toBeAttached();
  const choose = panel.locator("#restore-backup-transfer");
  await choose.scrollIntoViewIfNeeded();
  await expect(choose).toBeInViewport();
  const opened = page.waitForEvent("filechooser");
  await choose.click();
  const chooser = await opened;
  expect(await chooser.element().getAttribute("id")).toBe("backup-file-transfer");
  await overflow();
  await expectHarnessClean(page, errors);
});
