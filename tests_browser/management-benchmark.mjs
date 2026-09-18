// Reproducible structural/latency benchmark against the shipped mock backend.
// Usage: node tests_browser/management-benchmark.mjs http://127.0.0.1:4173 output.json
// Serve JS/MJS with a JavaScript MIME type. No live HA credentials are used.
import {chromium} from "@playwright/test";
import {writeFile} from "node:fs/promises";
const base = process.argv[2] || "http://127.0.0.1:4173";
const output = process.argv[3] || "management-benchmark.json";
const latency = 40;
const browser = await chromium.launch({headless:true});
const samples = [];
try {
  for (let run = 0; run < 5; run += 1) {
    const page = await browser.newPage();
    await page.route("**/tests_browser/harness.mjs", async route => {
      const response = await route.fetch();
      const source = (await response.text()).replace("calls.push(structuredClone(message));", `await new Promise(resolve => setTimeout(resolve, ${latency}));\n    calls.push(structuredClone(message));`);
      await route.fulfill({response, body:source});
    });
    await page.addInitScript(() => {
      window.benchmarkWrites = [];
      for (const proto of [Element.prototype, ShadowRoot.prototype]) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, "innerHTML");
        Object.defineProperty(proto, "innerHTML", {...descriptor, set(value) {
          if (this instanceof ShadowRoot || this.tagName === "MAIN" || this.id === "eoc-dialog-host") {
            window.benchmarkWrites.push(this instanceof ShadowRoot ? "shell" : this.tagName === "MAIN" ? "main" : "dialogs");
          }
          return descriptor.set.call(this, value);
        }});
      }
    });
    await page.coverage.startJSCoverage();
    await page.goto(`${base}/tests_browser/fixture.html?route=overview`);
    await page.waitForFunction(() => window.browserHarness?.panel?._result?.usage && !browserHarness.panel._busy);
    const cold = await page.evaluate(() => ({
      ms:Math.round(performance.now()),
      calls:browserHarness.calls.filter(c => c.type.endsWith("/management")).map(c => `${c.section || "root"}/${c.action}`),
      writes:[...benchmarkWrites],
      frontendBytes:performance.getEntriesByType("resource").filter(r => r.name.includes("/frontend/") && r.name.endsWith(".js")).reduce((sum,r) => sum + r.decodedBodySize, 0),
    }));
    const coverage = await page.coverage.stopJSCoverage();
    cold.evaluatedModules = coverage.filter(entry => entry.url.includes("/frontend/") && entry.functions.some(fn => fn.ranges.some(range => range.count > 0))).length;
    // Let independent overview decorators finish before measuring navigation.
    await page.waitForTimeout(latency * 2);
    const routes = [];
    for (const [p,s] of [["assistant","basics"],["data-memory","memories"],["data-memory","conversations"],["data-memory","knowledge"],["overview",null],["data-memory","knowledge"]]) {
      routes.push(await page.evaluate(async ([p,s]) => {
        const host = browserHarness.panel;
        const before = browserHarness.calls.length;
        benchmarkWrites.length = 0;
        const started = performance.now();
        await host._navigate(p,s);
        const elapsed = performance.now() - started;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return {route:host._viewKey(), ms:Math.round(elapsed), calls:browserHarness.calls.slice(before).filter(c => c.type.endsWith("/management")).map(c => `${c.section || "root"}/${c.action}`), writes:[...benchmarkWrites], error:host._error};
      }, [p,s]));
    }
    const repeatedRender = await page.evaluate(() => {
      benchmarkWrites.length = 0;
      browserHarness.panel._render();
      browserHarness.panel._render();
      return [...benchmarkWrites];
    });
    const expiredKnowledge = await page.evaluate(async () => {
      const host = browserHarness.panel;
      const key = host._sectionCacheKey();
      await host._navigate("overview");
      host._eocSectionCacheTimes.set(key, Date.now() - 31_000);
      const render = host._render;
      let usable = null;
      const start = performance.now();
      host._render = function(...args) {
        const result = render.apply(this, args);
        if (usable === null && !this._busy && this.shadowRoot.querySelector("#add-source")) usable = Math.round(performance.now() - start);
        return result;
      };
      await host._navigate("data-memory", "knowledge");
      host._render = render;
      return {firstUsableMs:usable, refreshMs:Math.round(performance.now() - start)};
    });
    samples.push({cold, routes, repeatedRender, expiredKnowledge, errors:await page.evaluate(() => [...browserHarness.windowErrors,...browserHarness.rejections])});
    await page.close();
  }
  await writeFile(output, JSON.stringify({backendLatencyMs:latency, samples}, null, 2) + "\n");
  console.log(`Wrote ${samples.length} samples to ${output}`);
} finally { await browser.close(); }

