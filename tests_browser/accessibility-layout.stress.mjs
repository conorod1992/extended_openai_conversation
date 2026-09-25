import {expect, test} from "@playwright/test";
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
      await expect(panel.locator(".top-nav")).toBeVisible();
      await expect(panel).toHaveCount(1);
      checked++;
    }
  }
  await expectHarnessClean(page, errors);
  await testInfo.attach("layout-pages", {body: JSON.stringify({pages: checked, widths: [390, 1600]}), contentType: "application/json"});
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
  await create.focus();
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
