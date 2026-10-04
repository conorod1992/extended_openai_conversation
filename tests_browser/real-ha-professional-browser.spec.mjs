import {expect, test} from "@playwright/test";
import {stat} from "node:fs/promises";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";
import {acceptConfirmation} from "./browser-helpers.mjs";
import {retentionGrowth, sampleRetainedRuntime} from "./browser-retention-metrics.mjs";

const enabled = process.env.EOAI_PROFESSIONAL_BROWSER === "1";
test.skip(!enabled, "dedicated professional genuine-HA browser profile");

test("production-sized backup crosses real browser and HA websocket in multiple chunks", async ({context, page}) => {
  test.setTimeout(120000);
  const panel = await openColdHaRoute(context, page, "usage-maintenance/backup-restore");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.__transferCalls = [];
    host.hass.callWS = async message => {
      const result = await original(message);
      if (message.type === "extended_openai_conversation_responses/management/backup_transfer") {
        window.__transferCalls.push({message: structuredClone(message), result: structuredClone(result)});
      }
      return result;
    };
  });

  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const download = await downloadPromise;
  expect(await download.failure()).toBeNull();
  const path = await download.path();
  expect(path).toBeTruthy();
  const size = (await stat(path)).size;

  const transfer = await page.evaluate(() => window.__transferCalls);
  const start = transfer.find(item => item.message.action === "export_start");
  expect(start).toBeTruthy();
  expect(start.result.chunk_size).toBe(512 * 1024);
  expect(start.result.chunk_count).toBeGreaterThanOrEqual(2);
  expect(size).toBe(start.result.size);
  expect(transfer.filter(item => item.message.action === "export_chunk")).toHaveLength(start.result.chunk_count);

  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  const afterUpload = await page.evaluate(() => window.__transferCalls);
  const importStart = afterUpload.find(item => item.message.action === "import_start");
  expect(importStart).toBeTruthy();
  expect(importStart.result.chunk_size).toBe(512 * 1024);
  expect(afterUpload.filter(item => item.message.action === "import_chunk").length).toBeGreaterThanOrEqual(2);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  const completed = await page.evaluate(() => window.__transferCalls);
  expect(completed.filter(item => item.message.action === "import_restore")).toHaveLength(1);
});

test("genuine native controls survive repeated mounts without duplicate saved writes", async ({context, page}, testInfo) => {
  test.setTimeout(120000);
  const panel = await openColdHaRoute(context, page, "capabilities/functions");
  const calls = await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.__professionalWsCalls = [];
    host.hass.callWS = async message => {
      window.__professionalWsCalls.push(structuredClone(message));
      return original(message);
    };
    return true;
  });
  expect(calls).toBe(true);

  // Seed one stable tool so each editor mount uses the real native YAML adapter.
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const yaml = "spec:\n  name: professional_native_tool\n  description: Native retention probe\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: retention-ok\n";
  await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), yaml);
  await panel.locator("#tool-validate").click();
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
  await panel.locator("#tool-save").click();
  await expect(panel.locator('[data-tool-key="professional_native_tool"]')).toBeVisible();

  let session = null;
  const samples = [];
  if (testInfo.project.name === "chromium") session = await page.context().newCDPSession(page);
  try {
    for (let index = 0; index < 24; index++) {
      await panel.locator('[data-tool-key="professional_native_tool"] .edit-tool').click();
      await expect(panel.locator("#tool-yaml-native")).toBeVisible();
      await panel.locator("#tool-cancel").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);

      await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
      await panel.locator('[data-config="exposed_entities_enabled"]').check();
      const picker = panel.locator("#exposed-entity-picker");
      await expect(picker).toBeVisible();
      await picker.locator("ha-picker-field").click();
      await page.keyboard.press("Escape");
      await panel.evaluate(host => host._navigate("capabilities", "functions"));
      await expect(panel.locator('[data-tool-key="professional_native_tool"]')).toBeVisible();

      if (session && index >= 8 && index % 2 === 0) {
        samples.push({window:index, ...await sampleRetainedRuntime(session)});
      }
    }
    if (session) expect(retentionGrowth(samples), JSON.stringify(samples)).toEqual([]);
  } finally {
    if (session) await session.detach();
  }

  const wsCalls = await page.evaluate(() => window.__professionalWsCalls);
  const saves = wsCalls.filter(call => call.section === "tools" && call.action === "save");
  expect(saves).toHaveLength(1);
});

test("browser timezone and locale do not rewrite HA wall-clock settings", async ({context, page}) => {
  test.setTimeout(90000);
  const panel = await openColdHaRoute(context, page, "capabilities/quiet-hours");
  const resolved = await page.evaluate(() => ({
    locale: Intl.DateTimeFormat().resolvedOptions().locale,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    touch: navigator.maxTouchPoints,
  }));
  if (process.env.EOAI_EXPECT_BROWSER_LOCALE) {
    expect(resolved.locale.toLowerCase()).toContain(process.env.EOAI_EXPECT_BROWSER_LOCALE.toLowerCase());
  }
  if (process.env.EOAI_EXPECT_BROWSER_TIMEZONE) {
    expect(resolved.timezone).toBe(process.env.EOAI_EXPECT_BROWSER_TIMEZONE);
  }

  await panel.locator("#qh-start").fill("23:00");
  await panel.locator("#save-page").click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);
  const saved = await panel.evaluate(host => host._call("quiet_hours", "get"));
  expect(saved.config.start).toBe("23:00");

  await page.reload();
  await expect(panel.locator("#qh-start")).toHaveValue("23:00");
});
