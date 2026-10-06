import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("suspend-resume simulation settles one pending save after clock and visibility discontinuity", async ({page}) => {
  test.setTimeout(60_000);
  const errors = trackPageErrors(page);
  await page.addInitScript(() => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", {configurable: true, get: () => visibility});
    Object.defineProperty(document, "hidden", {configurable: true, get: () => visibility === "hidden"});
    window.__setEoaiVisibility = value => {
      visibility = value;
      document.dispatchEvent(new Event("visibilitychange"));
    };
    const realNow = Date.now.bind(Date);
    let offset = 0;
    Date.now = () => realNow() + offset;
    window.__advanceEoaiClock = milliseconds => { offset += milliseconds; };
  });

  await page.goto(fixtureUrl("assistant/basics"));
  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await title.fill("Saved after simulated sleep");

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let release;
    window.__sleepProbe = {calls: 0, started: false, release: () => release?.()};
    hass.callWS = async message => {
      if (message.section === "configuration" && ["save", "update"].includes(message.action)) {
        window.__sleepProbe.calls++;
        window.__sleepProbe.started = true;
        await new Promise(resolve => { release = resolve; });
      }
      return original(message);
    };
  });

  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.__sleepProbe.started)).toBe(true);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();

  await page.evaluate(() => {
    window.__setEoaiVisibility("hidden");
    window.__advanceEoaiClock(6 * 60 * 60 * 1000);
  });
  expect(await page.evaluate(() => document.hidden)).toBe(true);
  await page.evaluate(() => window.__setEoaiVisibility("visible"));
  expect(await page.evaluate(() => document.visibilityState)).toBe("visible");

  await page.evaluate(() => window.__sleepProbe.release());
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  expect(await page.evaluate(() => window.__sleepProbe.calls)).toBe(1);

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Saved after simulated sleep");
  await expectHarnessClean(page, errors);
});

test("clipboard denial reports a recoverable error without breaking the preview", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async () => {
          throw new DOMException("Clipboard permission denied", "NotAllowedError");
        },
      },
    });
    document.execCommand = command => command === "copy" ? false : false;
  });

  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#preview-request")).toBeVisible();
  await panel.evaluate(host => {
    const original = host._call.bind(host);
    host._call = (section, action, ...args) => action === "request_preview"
      ? Promise.resolve({sections: [{label: "System prompt", content: "Clipboard denial probe", character_count: 22}], total_character_count: 22})
      : original(section, action, ...args);
  });
  await panel.locator("#preview-request").click();
  await expect(panel.locator("#prompt-preview-dialog")).toHaveJSProperty("open", true);
  const copy = panel.locator("#copy-prompt-preview");
  await expect(copy).toBeEnabled();
  await copy.click();
  await expect(panel.locator("#toast")).toContainText(/Unable to copy request/i);
  await expect(panel.locator("#prompt-preview-dialog")).toHaveJSProperty("open", true);
  await expect(copy).toBeEnabled();

  await panel.locator("#prompt-preview-close").click();
  await expect(panel.locator("#prompt-preview-dialog")).not.toBeVisible();
  await expectHarnessClean(page, errors);
});
