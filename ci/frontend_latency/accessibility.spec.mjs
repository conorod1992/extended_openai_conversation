import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {createRequire} from "node:module";
import {expect, test} from "@playwright/test";
import {LATENCY_ROUTES, waitForManagementRouteReady} from "./routes.mjs";
import {openColdHaRoute} from "../../tests_browser/real-ha-shell-helpers.mjs";
const require = createRequire(import.meta.url);
const output = process.env.EOAI_ACCESSIBILITY_OUTPUT;
test.skip(!process.env.REAL_HA_FRONTEND_URL || !output, "overnight genuine HA harness required");

export const NARROW_ROUTES = ["assistant-basics", "assistant-voice", "capabilities-request-rules", "capabilities-functions", "usage-maintenance-backup-restore"];

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
          await expect.poll(() => page.locator("home-assistant").evaluate(host => host.hass.themes.darkMode)).toBe(theme === "dark");
          await page.evaluate(() => document.fonts.ready);
          await page.addScriptTag({path:axePath});
          const scan = async state => {
            await settlePanel(panel);
            const result = await panel.evaluate(async host => {
              const result = await window.axe.run(host, {runOnly:{type:"tag", values:["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}});
              const compact = item => ({id:item.id, impact:item.impact, nodes:item.nodes.map(node => ({target:node.target, failureSummary:node.failureSummary}))});
              const yaml = host.shadowRoot.querySelector("#tool-yaml-native");
              const content = yaml?.shadowRoot?.querySelector("ha-code-editor")?.shadowRoot?.querySelector(".cm-content");
              const native_editor = content ? {role:content.getAttribute("role"), name:content.getAttribute("aria-label"), tabIndex:content.tabIndex, contenteditable:content.getAttribute("contenteditable"), outer_name:yaml.getAttribute("aria-label")} : null;
              return {native_editor, axe_version:result.testEngine.version, passes:result.passes.length, violations:result.violations.map(compact), incomplete:result.incomplete.map(compact)};
            });
            expect(result.passes).toBeGreaterThan(0);
            scans.push({route:route.name, theme, width, state, ...result});
            await mkdir(dirname(output), {recursive:true});
            await writeFile(output, JSON.stringify({scans, browser_environment:{chromium:browser.version(), playwright:require("@playwright/test/package.json").version, axe:require("axe-core/package.json").version}}, null, 2));
          };
          await scan("route");
          if (route.name === "capabilities-functions") {
            await panel.locator("#add-tool").click();
            await expect(panel.locator("#tool-yaml-native")).toBeVisible();
            await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(host => Boolean(host.codemirror))).toBe(true);
            await expect(panel.locator("#tool-yaml-native .cm-content")).toHaveAttribute("contenteditable", "true");
            await scan("tool-editor");
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
