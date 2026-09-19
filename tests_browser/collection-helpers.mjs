import {expect} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

export async function openCollection(page, route, size = 40) {
  await page.goto(fixtureUrl(route));
  await page.waitForFunction(() => window.browserHarness?.panel?._result && !browserHarness.panel._busy);
  await page.evaluate((size) => {
    const state = browserHarness.getState();
    const tool = state.configuration.config.functions[0];
    state.configuration.config.functions = Array.from({length: size}, (_, i) => ({
      ...structuredClone(tool), spec: {...structuredClone(tool.spec), name: `tool_${i}`, description: `Fixture function ${i}`},
    }));
    state.configuration.config.function_groups = Array.from({length: 4}, (_, i) => ({
      ...structuredClone(state.configuration.config.function_groups[0]), id: `group-${i}`, name: `Group ${i}`,
      functions: state.configuration.config.functions.filter((_, n) => n % 4 === i).map(t => t.spec.name),
    }));
    for (const tool of state.configuration.config.functions) {
      state.toolYamls[tool.spec.name] = `spec:\n  name: ${tool.spec.name}\n  description: ${tool.spec.description}\nfunction:\n  type: script\n  sequence: []\n`;
    }
    const rule = state.requestRules.rules[0];
    state.requestRules.rules = Array.from({length: size}, (_, i) => ({
      ...structuredClone(rule), id: `rule-${i + 1}`, name: `Fixture rule ${i}`, phrases: [`fixture phrase ${i}`], order: i,
    }));
    state.nextRuleId = size + 1;
    localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
  }, size);
  await page.goto(fixtureUrl(route));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(route.endsWith("functions") ? ".tool-card" : ".request-rule-card")).toHaveCount(size);
  await page.evaluate(() => {
    browserHarness.panel.shadowRoot.querySelectorAll(".function-group-card details").forEach(d => { d.open = true; });
  });
  return panel;
}

export async function beginCollectionMeasure(page) {
  await page.evaluate(() => {
    const root = browserHarness.panel.shadowRoot;
    const main = root.querySelector("main");
    const cards = [...root.querySelectorAll(".tool-card,.request-rule-card")];
    const groups = [...root.querySelectorAll(".function-group-card")];
    const records = [];
    const observer = new MutationObserver(items => records.push(...items));
    observer.observe(main, {subtree: true, childList: true, attributes: true, characterData: true});
    const originalRender = browserHarness.panel._render;
    let renders = 0;
    browserHarness.panel._render = function(...args) { renders += 1; return originalRender.apply(this, args); };
    window.collectionMeasurement = () => {
      records.push(...observer.takeRecords());
      observer.disconnect();
      browserHarness.panel._render = originalRender;
      const elements = nodes => [...nodes].reduce((sum, node) => sum + (node.nodeType === 1 ? 1 + node.querySelectorAll("*").length : 0), 0);
      return {
        records: records.length,
        elementChanges: records.reduce((sum, r) => sum + elements(r.addedNodes) + elements(r.removedNodes), 0),
        mainChildReplacements: records.filter(r => r.type === "childList" && r.target === main).length,
        retainedCards: cards.filter(n => n.isConnected).length,
        retainedGroups: groups.filter(n => n.isConnected).length,
        renders,
      };
    };
  });
}

export async function finishCollectionMeasure(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.evaluate(() => collectionMeasurement());
}
