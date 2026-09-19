// Structural benchmark of actual UI mutations. No wall-clock timing claims.
// Serve the checkout on :4173, then: node tests_browser/collection-benchmark.mjs output.json
import {chromium, expect} from "@playwright/test";
import {writeFile} from "node:fs/promises";
import {openCollection, beginCollectionMeasure, finishCollectionMeasure} from "./collection-helpers.mjs";
const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.CHROMIUM_EXECUTABLE_PATH} : {})});
const results = {};
try {
  for (const [name, route, selector] of [
    ["functions", "capabilities/functions", '.tool-enabled[data-index="0"]'],
    ["rules", "capabilities/request-rules", '.rule-enabled[data-id="rule-1"]'],
  ]) {
    const page = await browser.newPage({baseURL: "http://127.0.0.1:4173"});
    const panel = await openCollection(page, route);
    await beginCollectionMeasure(page);
    await panel.locator(selector).uncheck();
    await expect(panel.locator(selector)).not.toBeChecked();
    await expect(panel.locator(selector)).toBeEnabled();
    // Await the authoritative mutation rather than observing a browser's immediate checkbox change.
    await page.waitForFunction(name => name === "functions"
      ? browserHarness.panel._draft.functions[0].enabled === false
      : browserHarness.panel._result.rules[0].enabled === false, name);
    results[name] = await finishCollectionMeasure(page);
    await beginCollectionMeasure(page);
    const search = panel.locator(name === "functions" ? "#tool-search" : "#rule-search");
    await search.fill("fixture");
    results[`${name}Search`] = await finishCollectionMeasure(page);
    await page.close();
  }
  console.log(JSON.stringify(results, null, 2));
  await writeFile(process.argv[2] || "collection-benchmark.json", JSON.stringify({size: 40, results}, null, 2) + "\n");
} finally { await browser.close(); }
