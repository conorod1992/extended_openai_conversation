// Structural DOM work only, not a wall-clock speed benchmark.
// python3 -m http.server 4173 --bind 127.0.0.1
// CHROMIUM_EXECUTABLE_PATH=/path/to/chromium node tests_browser/data-collection-benchmark.mjs output.json
import {chromium, expect} from "@playwright/test";
import {writeFile} from "node:fs/promises";
import {openDataCollection, beginDataMeasure, finishDataMeasure} from "./data-collection-helpers.mjs";
import {acceptConfirmation} from "./browser-helpers.mjs";
const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.CHROMIUM_EXECUTABLE_PATH} : {})});
const results = {};
try {
  for (const kind of ["knowledge", "persistent", "temporary", "standalone"]) {
    const page = await browser.newPage({baseURL: process.env.BENCHMARK_BASE_URL || "http://127.0.0.1:4173"});
    const panel = await openDataCollection(page, kind, kind === "knowledge" ? 60 : 100);
    const standalone = kind === "standalone", knowledge = kind === "knowledge", temporary = kind === "temporary";
    const list = panel.locator(standalone ? "#memories" : knowledge ? ".knowledge-list" : ".memory-list");
    const card = list.locator("article").first();
    if (!temporary) {
      await card.locator(standalone ? '[aria-label="Edit memory"]' : knowledge ? ".source-edit-button" : ".memory-edit-button").click();
      const field = panel.locator(standalone ? "#memoryContent" : knowledge ? "#knowledge-title" : "#memory-content");
      await expect(field).toBeEnabled();
      await field.fill("Edited fixture record");
      await beginDataMeasure(page);
      await panel.locator(standalone ? "#dialogSave" : knowledge ? "#knowledge-save" : "#memory-save").click();
      await expect(list.getByText("Edited fixture record", {exact: true})).toBeVisible();
      results[`${kind}Edit`] = await finishDataMeasure(page);
    }
    await card.locator(standalone ? '[aria-label="Delete memory"]' : knowledge ? ".delete-source" : temporary ? ".delete-temporary" : ".delete-memory").click();
    await beginDataMeasure(page);
    if (standalone) await panel.locator("#confirmAccept").click();
    else await acceptConfirmation(panel);
    await expect(list.locator("article")).toHaveCount(temporary ? 11 : knowledge ? 59 : 99);
    results[`${kind}Delete`] = await finishDataMeasure(page);
    if (!temporary) {
      await beginDataMeasure(page);
      await panel.locator(standalone ? "#search" : "#list-search").fill(knowledge ? "Source 1" : "Memory 1");
      if (kind === "persistent") {
        await page.waitForFunction(() => dataCollectionBackend.calls.some(call => call.action === "search"));
        // This is an async completion barrier, not an elapsed-time measurement.
        await page.waitForFunction(() => browserHarness.panel._managementBrowserState.memoryQuery === "memory 1");
      }
      results[`${kind}Search`] = await finishDataMeasure(page);
    }
    if (kind === "knowledge") {
      await panel.locator("#list-search").fill("");
      await beginDataMeasure(page);
      await panel.evaluate(async host => {
        const key = host._sectionCacheKey(); host._eocSectionCacheTimes.set(key, 1);
        await host._loadSection(true);
      });
      results.knowledgeCachedRefresh = await finishDataMeasure(page);
      await page.evaluate(() => {
        const sources = dataCollectionBackend.state.sources;
        sources[1].title = "Changed by background refresh"; sources[1].enabled = false;
        sources.splice(2, 1);
        sources.splice(1, 0, {...sources[0], source_id: "new-background-source", title: "New background source"});
      });
      await beginDataMeasure(page);
      await panel.evaluate(async host => { host._eocSectionCacheTimes.set(host._sectionCacheKey(), 1); await host._loadSection(true); });
      results.knowledgeChangedRefresh = await finishDataMeasure(page);
    }
    if (kind === "persistent") {
      await panel.locator("#list-search").fill("");
      await page.waitForFunction(() => browserHarness.panel._managementBrowserState.memoryQuery === "");
      await list.locator("article").nth(20).locator(".memory-edit-button").click();
      await panel.locator("#memory-category").fill("Moved category");
      await beginDataMeasure(page); await panel.locator("#memory-save").click();
      await expect(list.getByText(/Moved category/)).toBeVisible();
      results.persistentCategoryMove = await finishDataMeasure(page);
    }
    if (temporary) {
      await beginDataMeasure(page); await panel.locator("#list-search").fill("Temporary 1");
      results.temporarySearch = await finishDataMeasure(page);
    }
    if (standalone) {
      await panel.locator("#search").fill("");
      await beginDataMeasure(page);
      await panel.locator("#categories button").filter({hasText: "category-2"}).click();
      results.standaloneCategoryFilter = await finishDataMeasure(page);
      await panel.locator("#categories button").first().click();
      await list.locator("article").first().locator('[aria-label="Edit memory"]').click();
      await panel.locator("#memoryCategory").fill("moved-category");
      await beginDataMeasure(page);
      await panel.locator("#dialogSave").click();
      await expect(panel.locator("#categories")).toContainText("moved-category 1");
      results.standaloneCategoryMove = await finishDataMeasure(page);
    }
    await page.close();
  }
  const output = {knowledgeSize: 60, persistentSize: 100, temporarySize: 12, results};
  console.log(JSON.stringify(output, null, 2));
  await writeFile(process.argv[2] || "data-collection-benchmark.json", JSON.stringify(output, null, 2) + "\n");
} finally { await browser.close(); }
