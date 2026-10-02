import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {createRequire} from "node:module";
import {expect, test} from "@playwright/test";
import {LATENCY_ROUTES, waitForManagementRouteReady} from "./routes.mjs";
import {openColdHaRoute} from "../../tests_browser/real-ha-shell-helpers.mjs";
import {replaceNativeYaml} from "../../tests_browser/real-ha-shell-helpers.mjs";
import {openFunctionAddMenu} from "../../tests_browser/browser-helpers.mjs";
const require = createRequire(import.meta.url);
const output = process.env.EOAI_ACCESSIBILITY_OUTPUT;
test.skip(!process.env.REAL_HA_FRONTEND_URL || !output, "overnight genuine HA harness required");

export const NARROW_ROUTES = ["assistant-basics", "assistant-voice", "capabilities-request-rules", "capabilities-functions", "usage-maintenance-backup-restore"];
export const INTERACTIVE_STATES = {"capabilities-functions": ["tool-invalid", "tool-save-error", "destructive-confirmation", "destructive-error"], "usage-maintenance-backup-restore": ["restore-confirmation"]};

async function settlePanel(panel) {
  // Route readiness precedes native/Lit descendants and deferred card hydration.
  await panel.evaluate(async host => {
    const roots = new Set();
    let changedAt = performance.now();
    const observer = new MutationObserver(() => { changedAt = performance.now(); observe(host); });
    const observe = element => {
      if (element.shadowRoot && !roots.has(element.shadowRoot)) {
        roots.add(element.shadowRoot);
        observer.observe(element.shadowRoot, {subtree:true, childList:true, attributes:true, characterData:true});
      }
      for (const child of element.shadowRoot?.querySelectorAll("*") || []) observe(child);
    };
    observe(host);
    const started = performance.now();
    try {
      while (performance.now() - changedAt < 350) {
        if (performance.now() - started > 10000) throw new Error("EOAI descendants did not settle before semantic scan");
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    } finally { observer.disconnect(); }
  });
}

test("scan genuine HA route and editor accessibility semantics", async ({browser}) => {
  test.setTimeout(300000);
  const scans = [];
  const axePath = require.resolve("axe-core/axe.min.js");
  for (const theme of ["light", "dark"]) {
    for (const width of [1280, 390]) {
      const context = await browser.newContext({colorScheme:theme, viewport:{width, height:900}, bypassCSP:true});
      const page = await context.newPage();
      try {
        await openColdHaRoute(context, page, "overview");
        for (const route of LATENCY_ROUTES.filter(route => width === 1280 || NARROW_ROUTES.includes(route.name))) {
          await page.goto(`${process.env.REAL_HA_FRONTEND_URL}/extended-openai/${route.path}`, {waitUntil:"domcontentloaded"});
          await waitForManagementRouteReady(page, route, 30000);
          const panel = page.locator("extended-openai-management-panel");
          const themeColour = () => panel.evaluate(host => {
            const background = getComputedStyle(host).getPropertyValue("--primary-background-color").trim();
            const canvas = document.createElement("canvas");
            canvas.width = canvas.height = 1;
            const drawing = canvas.getContext("2d");
            drawing.fillStyle = background;
            drawing.fillRect(0, 0, 1, 1);
            const [r, g, b] = drawing.getImageData(0, 0, 1, 1).data;
            return {background, brightness:(r + g + b) / 3};
          });
          await expect.poll(async () => {
            const colour = await themeColour();
            return Boolean(colour.background) && (colour.brightness < 128) === (theme === "dark");
          }).toBe(true);
          await page.evaluate(() => document.fonts.ready);
          await page.addScriptTag({path:axePath});
          const scan = async state => {
            await settlePanel(panel);
            const result = await panel.evaluate(async (host, state) => {
              const scoped = {"tool-invalid":"#tool-dialog", "tool-save-error":"#tool-dialog", "destructive-confirmation":"#confirm-dialog", "destructive-error":"#toast", "restore-confirmation":"#restore-dialog", "picker-populated":"#config-voice_default_user_picker"};
              const scope = scoped[state] ? host.shadowRoot.querySelector(scoped[state]) : host;
              if (!scope) throw new Error(`Missing scan scope for ${state}`);
              const result = await window.axe.run(scope, {runOnly:{type:"tag", values:["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}});
              const compact = item => ({id:item.id, impact:item.impact, nodes:item.nodes.map(node => ({target:node.target, failureSummary:node.failureSummary}))});
              const yaml = host.shadowRoot.querySelector("#tool-yaml-native");
              const content = yaml?.shadowRoot?.querySelector("ha-code-editor")?.shadowRoot?.querySelector(".cm-content");
              const native_editor = content ? {role:content.getAttribute("role"), name:content.getAttribute("aria-label"), tabIndex:content.tabIndex, contenteditable:content.getAttribute("contenteditable"), outer_name:yaml.getAttribute("aria-label")} : null;
              return {native_editor, axe_version:result.testEngine.version, passes:result.passes.length, violations:result.violations.map(compact), incomplete:result.incomplete.map(compact)};
            }, state);
            expect(result.passes).toBeGreaterThan(0);
            scans.push({route:route.name, theme, theme_colour:await themeColour(), width, state, ...result});
            await mkdir(dirname(output), {recursive:true});
            await writeFile(output, JSON.stringify({scans, diagnostic_picker:process.env.EOAI_ACCESSIBILITY_DIAGNOSTIC_PICKER === "1", browser_environment:{chromium:browser.version(), playwright:require("@playwright/test/package.json").version, axe:require("axe-core/package.json").version}}, null, 2));
          };
          await scan("route");
          if (route.name === "capabilities-functions") {
            await openFunctionAddMenu(panel, "#add-tool");
            await expect(panel.locator("#tool-yaml-native")).toBeVisible();
            await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(host => Boolean(host.codemirror))).toBe(true);
            await expect(panel.locator("#tool-yaml-native .cm-content")).toHaveAttribute("contenteditable", "true");
            await scan("tool-editor");
            await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), "spec: [invalid");
            await panel.locator("#tool-validate").click();
            await expect(panel.locator("#tool-error")).toHaveClass(/invalid/);
            await scan("tool-invalid");
            const tool = {spec:{name:"accessibility_saved_tool", description:"Preserved saved tool", parameters:{type:"object", properties:{}}}, function:{type:"template", value_template:"healthy"}};
            await panel.evaluate((host, tool) => host._call("tools", "save", {tool}), tool);
            const yaml = `spec:\n  name: accessibility_saved_tool\n  description: Editable failed draft\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: healthy\n`;
            await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), yaml);
            await panel.locator("#tool-save").click();
            await expect(panel.locator("#tool-error")).toContainText(/already exists/);
            await expect(panel.locator("#tool-yaml-native")).toBeVisible();
            await scan("tool-save-error");
            await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), yaml.replace("accessibility_saved_tool", "accessibility_recovered_tool"));
            await page.keyboard.press("Control+s");
            await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
            const card = panel.locator('[data-tool-key="accessibility_recovered_tool"]');
            await expect(card).toBeVisible();
            await panel.evaluate(host => {
              const original = host.hass.callWS.bind(host.hass);
              let failed = false;
              host.hass.callWS = message => {
                if (!failed && message.section === "tools" && message.action === "delete") {
                  failed = true;
                  return Promise.reject(new Error("Accessibility destructive transport error"));
                }
                return original(message);
              };
            });
            await card.locator(".delete-tool").click();
            await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
            await expect.poll(() => panel.locator("#confirm-dialog").evaluate(element => element.contains(element.getRootNode().activeElement))).toBe(true);
            await scan("destructive-confirmation");
            await panel.locator("#confirm-accept").click();
            await expect(panel.locator("#toast")).toContainText("Accessibility destructive transport error");
            await expect(card).toBeVisible();
            await scan("destructive-error");
            await card.locator(".delete-tool").click();
            await panel.locator("#confirm-accept").click();
            await expect(card).toHaveCount(0);
            await panel.evaluate(host => host._call("tools", "delete", {name:"accessibility_saved_tool", confirm:true}));
          }
          if (route.name === "assistant-voice" && process.env.EOAI_ACCESSIBILITY_DIAGNOSTIC_PICKER === "1") {
            await panel.locator('[data-config="voice_scope_policy"]').selectOption("device_mapping");
            await panel.locator('[data-config="voice_unmapped_policy"]').selectOption("default_user");
            const picker = panel.locator("#config-voice_default_user_picker");
            await picker.locator("ha-picker-field").click();
            await picker.locator("ha-combo-box-item").filter({hasText:process.env.REAL_HA_PICKER_USER_NAME}).locator("button").click();
            await expect(picker).toContainText(process.env.REAL_HA_PICKER_USER_NAME);
            await scan("picker-populated");
            await panel.locator("#revert-config").click();
          }
          if (route.name === "usage-maintenance-backup-restore") {
            await panel.locator("#transfer-export-mode").selectOption("full");
            const downloaded = page.waitForEvent("download");
            await panel.locator("#create-backup-transfer").click();
            const download = await downloaded;
            expect(await download.failure()).toBeNull();
            await panel.locator("#backup-file-transfer").setInputFiles(await download.path());
            await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
            await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
            await scan("restore-confirmation");
            await panel.locator("#restore-transfer-cancel").click();
          }
          if (route.name === "capabilities-request-rules") {
            await panel.locator("#rule-add").click();
            await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
            await scan("rule-editor");
          }
        }
      } finally { await context.close(); }
    }
  }
});
