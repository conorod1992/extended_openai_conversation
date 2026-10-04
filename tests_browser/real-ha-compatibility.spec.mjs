import {expect, test} from "@playwright/test";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

test.skip(process.env.RUN_REAL_HA_BROWSER_COMPATIBILITY !== "1", "dedicated compact HA version matrix");

test("HA native components and actual Assist path remain compatible", async ({context, page}, testInfo) => {
  test.setTimeout(120000);
  const panel = await openColdHaRoute(context, page, "assistant/voice");
  const primary = await panel.locator("#agent").inputValue();
  const options = await panel.locator("#agent option").evaluateAll(nodes => nodes.map(node => node.value));
  expect(options).toHaveLength(2);
  await panel.locator("#agent").selectOption(options.find(value => value !== primary));
  await expect(panel.locator("#agent")).not.toHaveValue(primary);
  await panel.locator("#agent").selectOption(primary);
  await waitForManagementRouteReady(page, {name:"voice", path:"assistant/voice"}, 30000);
  const picker = panel.locator("#config-voice_default_user_picker");
  await panel.locator('[data-config="voice_scope_policy"]').selectOption("device_mapping");
  await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("default_user");
  await expect(picker).toBeVisible();
  await picker.locator("ha-picker-field").click();
  await picker.locator("ha-combo-box-item").filter({hasText:"Browser Frontend Shell Admin"}).locator("button").click();
  await expect(picker).toHaveJSProperty("value", process.env.REAL_HA_SMOKE_USER_ID);
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
  await page.reload();
  await expect(picker).toHaveJSProperty("value", process.env.REAL_HA_SMOKE_USER_ID);

  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  const entity = panel.locator("#exposed-entity-picker");
  await panel.locator('[data-config="exposed_entities_enabled"]').check();
  await expect(entity).toBeVisible();
  if (process.env.ENHANCED_EXECUTION_EVIDENCE === "1") {
    await entity.locator("ha-picker-field").click();
    const choice = entity.locator("ha-combo-box-item").filter({hasText:"sensor.cold_attribute_kitchen"}).locator("button");
    await choice.click();
    await expect(entity).toHaveJSProperty("value", "sensor.cold_attribute_kitchen");
  } else {
    // Preserve the existing PR smoke path; native selection is overnight-only.
    await entity.evaluate(element => {
      element.value = "sensor.cold_attribute_kitchen";
      element.dispatchEvent(new CustomEvent("value-changed", {detail:{value:element.value}, bubbles:true}));
    });
  }
  await expect(panel.locator("[data-exposed-editor]")).toContainText("sensor.cold_attribute_kitchen");
  await panel.locator('[data-exposed-attribute][data-attribute="battery_level"]').check();
  await panel.locator("[data-close-exposed-editor]").click();
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);

  await panel.evaluate(host => host._navigate("capabilities", "functions"));
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const yaml = "spec:\n  name: compatibility_tool\n  description: Compact native editor smoke\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: compatibility-result\n";
  await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), yaml);
  await panel.locator("#tool-validate").click();
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
  await panel.locator("#tool-save").click();
  await expect(panel.locator('[data-tool-key="compatibility_tool"]')).toBeVisible();

  await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
  await panel.locator("#rule-add").click();
  await panel.locator("#rule-name").fill("Compatibility route");
  await panel.locator("#rule-phrases").fill("compatibility smoke");
  await panel.locator("#rule-match").selectOption("equals");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-continue-to-ai").check();
  await panel.locator("#rule-add-conditions").click();
  const selector = panel.locator("#rule-condition-host > ha-selector");
  await expect(selector).toBeVisible();
  expect(await selector.evaluate(element => Boolean(element.hass?.localize))).toBe(true);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator(".request-rule-card").filter({hasText:"Compatibility route"})).toBeVisible();
  await panel.locator("#eoc-rule-live-test summary").click();
  await expect(panel.locator("#eoc-rule-live-test")).toHaveAttribute("data-eoc-bound", "");
  await panel.locator("#eoc-rule-live-text").fill("compatibility smoke");
  await panel.locator("#eoc-rule-live-run").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Compatibility Assist response");
  await expect(panel.locator("#eoc-rule-live-run")).toBeEnabled();
  if (testInfo.project.name === "webkit-mobile") {
    const mobile = await page.evaluate(() => ({
      touch: navigator.maxTouchPoints,
      coarse: matchMedia("(pointer: coarse)").matches,
      width: innerWidth,
      height: innerHeight,
    }));
    expect(mobile.touch).toBeGreaterThan(0);
    expect(mobile.coarse).toBe(true);
    expect(mobile.width).toBeLessThan(mobile.height);
    await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
    await panel.locator("#transfer-export-mode").selectOption("full");
    const downloaded = page.waitForEvent("download");
    await panel.locator("#create-backup-transfer").tap();
    const download = await downloaded;
    expect(await download.failure()).toBeNull();
    const path = await download.path();
    expect(path).toBeTruthy();
    await panel.locator("#backup-file-transfer").setInputFiles(path);
    await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
    // The dialog and its actions must fit the narrow viewport.
    const geometry = await panel.evaluate(host => {
      const dialog = host.shadowRoot.querySelector("#restore-dialog");
      const box = dialog.getBoundingClientRect();
      return {
        dialogWidth:dialog.clientWidth, dialogScroll:dialog.scrollWidth,
        left:box.left, right:box.right, viewport:innerWidth,
      };
    });
    expect(geometry.dialogScroll, JSON.stringify(geometry)).toBeLessThanOrEqual(geometry.dialogWidth + 1);
    expect(geometry.left).toBeGreaterThanOrEqual(0);
    expect(geometry.right).toBeLessThanOrEqual(geometry.viewport + 1);

    // Rotate the same touch context and prove dialog actions remain reachable.
    await page.setViewportSize({width: 844, height: 390});
    const landscape = await panel.evaluate(host => {
      const dialog = host.shadowRoot.querySelector("#restore-dialog");
      const apply = host.shadowRoot.querySelector("#restore-transfer-apply");
      const box = dialog.getBoundingClientRect();
      const action = apply.getBoundingClientRect();
      return {
        left:box.left, right:box.right, viewport:innerWidth,
        actionTop:action.top, actionBottom:action.bottom, viewportHeight:innerHeight,
      };
    });
    expect(landscape.left).toBeGreaterThanOrEqual(0);
    expect(landscape.right).toBeLessThanOrEqual(landscape.viewport + 1);
    expect(landscape.actionTop).toBeGreaterThanOrEqual(0);
    expect(landscape.actionBottom).toBeLessThanOrEqual(landscape.viewportHeight + 1);
    await panel.locator("#restore-transfer-cancel").tap();
    await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  }
});
