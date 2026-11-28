import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const headingStyle = heading => heading.evaluate(node => {
  const {fontSize, fontWeight, lineHeight} = getComputedStyle(node);
  return {fontSize, fontWeight, lineHeight};
});

for (const width of [390, 1600]) {
  test(`management headings, refresh controls, and Assistant actions line up at ${width}px`, async ({page}) => {
    await page.setViewportSize({width, height:900});
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/scheduled-actions", "&bundle=1"));
    const panel = page.locator("extended-openai-management-panel");
    const heading = panel.getByRole("heading", {name:"Scheduled Actions", level:1});
    const menu = panel.locator(".agent-actions-menu > summary");
    await expect(menu).toBeVisible();
    const originalMenu = await menu.elementHandle();
    const dimensions = await panel.locator("#refresh-scheduled-actions").evaluate(button => {
      const main = button.closest("main").getBoundingClientRect();
      return {left:main.left, gap:main.right - button.getBoundingClientRect().right};
    });
    const scheduledHeading = await headingStyle(heading);
    expect(Math.abs(dimensions.gap)).toBeLessThan(1);

    await panel.evaluate(host => host._navigate("usage-maintenance", "request-debug"));
    const debug = panel.locator("extended-openai-debug-panel");
    await expect(debug.getByRole("heading", {name:"Request debugging", level:1})).toBeVisible();
    expect(await headingStyle(debug.getByRole("heading", {name:"Request debugging", level:1}))).toEqual(scheduledHeading);
    await expect(menu).toBeVisible();
    expect(await originalMenu.evaluate(node => node.isConnected)).toBe(true);
    expect(await panel.locator("[data-eoc-main]").evaluate(node => node.getBoundingClientRect().left)).toBe(dimensions.left);
    expect(await debug.getByRole("button", {name:"Refresh", exact:true}).evaluate(node => node.getBoundingClientRect().right))
      .toBeCloseTo(await panel.locator("[data-eoc-main]").evaluate(node => node.getBoundingClientRect().right), 0);

    const capture = debug.getByRole("switch", {name:"Request debugging", exact:true});
    await expect(capture).toBeEnabled();
    const centered = () => capture.evaluate(node => {
      const track = node.nextElementSibling;
      const thumb = getComputedStyle(track, "::after");
      const offset = new DOMMatrixReadOnly(thumb.transform).f;
      return parseFloat(thumb.top) + offset + parseFloat(thumb.height) / 2 - track.clientHeight / 2;
    });
    expect(await centered()).toBeCloseTo(0);
    await capture.check();
    await expect(capture).toBeChecked();
    expect(await centered()).toBeCloseTo(0);
    if (width > 800) {
      // Simulate a width change equivalent to a classic vertical scrollbar.
      await page.setViewportSize({width:width - 15, height:900});
      expect(await panel.locator("[data-eoc-main]").evaluate(node => node.getBoundingClientRect().left)).toBe(dimensions.left);
    }
    await expectHarnessClean(page, errors);
  });
}

test("Assistant actions open the existing import on a cold non-editor route", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name:"Memories", level:1})).toBeVisible();
  await expect(panel.locator("#import-dialog")).toHaveCount(0);
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    host.hass.callWS = request => request.section === "configuration" && request.action === "import_preview"
      ? Promise.resolve({title:"Reviewed assistant", summary:{model:"gpt-5-mini", tools:0, function_groups:0, speech_rules:0}})
      : call(request);
  });
  const menu = panel.locator(".agent-actions-menu > summary");
  await menu.click();
  await expect(panel.getByRole("menuitem", {name:"Duplicate agent"})).toBeEnabled();
  await panel.getByRole("menuitem", {name:"Import configuration"}).click();
  const dialog = panel.getByRole("dialog", {name:"Import agent configuration"});
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Overwrite current agent").check();
  await dialog.getByLabel("Exported JSON or YAML").fill("Reviewed configuration");
  await dialog.getByRole("button", {name:"Validate & preview"}).click();
  await expect(dialog.getByRole("button", {name:"Import", exact:true})).toBeEnabled();
  expect(await panel.evaluate(host => host._configData.revision)).toBeTruthy();
  await dialog.getByRole("button", {name:"Cancel"}).click();
  await expect(menu).toBeFocused();
  await expect(panel.getByRole("heading", {name:"Memories", level:1})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("non-admins do not get the Assistant actions menu", async ({page}) => {
  await page.goto(fixtureUrl("overview", "&bundle=1&admin=0"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent option:checked")).toHaveText("Jarvis");
  await expect(panel.locator(".agent-actions-menu")).toHaveCount(0);
});
