import {expect} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";
export const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

export async function openDataCollection(page, kind, size = 100) {
  if (kind === "standalone") {
    await page.goto(`/tests_browser/memory-collections-fixture.html?size=${size}`);
    const panel = page.locator("extended-openai-memory-management-panel");
    await expect(panel.locator("#memories .memory-card")).toHaveCount(size);
    return panel;
  }
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".guide-search")).toBeVisible();
  await page.evaluate(async ({kind, size}) => {
    const {createDataCollectionBackend} = await import("/tests_browser/data-collection-backend.mjs");
    const backend = createDataCollectionBackend(size);
    window.dataCollectionBackend = backend;
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    browserHarness.hass.callWS = message => {
      if (["knowledge", "memories"].includes(message.section)) {
        browserHarness.calls.push(structuredClone(message));
        return backend.call(message);
      }
      return original(message);
    };
    if (kind === "temporary") browserHarness.panel._memoryKind = "temporary";
    await browserHarness.panel._navigate("data-memory", kind === "knowledge" ? "knowledge" : "memories");
  }, {kind, size});
  await expect(panel.locator(kind === "knowledge" ? ".knowledge-list .list-card" : ".memory-list .list-card")).toHaveCount(kind === "temporary" ? 12 : size);
  return panel;
}

export async function beginDataMeasure(page, selector = ".list-card,.memory-card") {
  await page.evaluate(selector => {
    const panel = browserHarness.panel, root = panel.shadowRoot;
    const main = root.querySelector("main"), cards = [...root.querySelectorAll(selector)];
    const input = root.querySelector("#list-search,#search");
    const records = [];
    const observer = new MutationObserver(items => records.push(...items));
    observer.observe(main, {subtree: true, childList: true, attributes: true, characterData: true});
    const original = panel._render;
    let renders = 0;
    if (original) panel._render = function(...args) { renders++; return original.apply(this, args); };
    window.finishDataMeasurement = () => {
      records.push(...observer.takeRecords()); observer.disconnect();
      if (original) panel._render = original;
      const count = nodes => [...nodes].reduce((sum, node) => sum + (node.nodeType === 1 ? 1 + node.querySelectorAll("*").length : 0), 0);
      return {
        records: records.length,
        elementsAdded: records.reduce((sum, record) => sum + count(record.addedNodes), 0),
        elementsRemoved: records.reduce((sum, record) => sum + count(record.removedNodes), 0),
        mainChildReplacements: records.filter(record => record.type === "childList" && record.target === main).length,
        retainedCards: cards.filter(node => node.isConnected).length,
        initialCards: cards.length,
        routeRenders: renders,
        inputRetained: input === root.querySelector("#list-search,#search"),
        inputFocused: input === root.activeElement,
        caret: input?.selectionStart ?? null,
      };
    };
  }, selector);
}
export async function finishDataMeasure(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.evaluate(() => finishDataMeasurement());
}
