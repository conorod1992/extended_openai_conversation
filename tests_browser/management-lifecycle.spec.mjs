import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("unchanged route retains controls and core dialog edits across updates and navigation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-source")).toBeVisible();
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Retained draft");
  await panel.locator("#knowledge-content").fill("An editor must survive unrelated renders.");
  const identity = await panel.evaluate((host) => {
    const root = host.shadowRoot;
    const button = root.querySelector("#add-source");
    const dialog = root.querySelector("#knowledge-dialog");
    const main = root.querySelector("main");
    host._render();
    host._render();
    return {button: button === root.querySelector("#add-source"), dialog: dialog === root.querySelector("#knowledge-dialog"), main: main === root.querySelector("main")};
  });
  expect(identity).toEqual({button:true, dialog:true, main:true});
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#knowledge-title")).toHaveValue("Retained draft");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => browserHarness.calls.filter(c => c.section === "knowledge" && c.action === "create").length)).toBe(1);
  expect(await panel.evaluate(async host => {
    const dialog = host.shadowRoot.querySelector("#knowledge-dialog");
    await host._navigate("overview");
    await host._navigate("data-memory", "knowledge");
    return dialog === host.shadowRoot.querySelector("#knowledge-dialog");
  })).toBe(true);
  await panel.locator("#add-source").click();
  await expect(panel.locator("#knowledge-title")).toHaveValue("");
  await expectHarnessClean(page, errors);
});

test("scope catalog is shared across data routes with a fixed TTL and mutation invalidation", async ({page}) => {
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-memory")).toBeVisible();
  const counts = await panel.evaluate(async host => {
    const calls = window.browserHarness.calls;
    const count = () => calls.filter(c => c.section === "scopes").length;
    const key = host._scopeCatalogKey();
    const fetchedAt = host._eocScopeCatalogTimes.get(key);
    await host._navigate("data-memory", "conversations");
    const shared = count();
    const unchangedTimestamp = host._eocScopeCatalogTimes.get(key) === fetchedAt;
    host._eocScopeCatalogTimes.set(key, Date.now() - 31_000);
    await host._navigate("data-memory", "memories");
    const expired = count();
    await host._call("memories", "add", {scope_id:host._scopeId, content:"Cache invalidation test", category:"general"});
    await host._loadSection();
    return {shared, unchangedTimestamp, expired, mutated:count()};
  });
  expect(counts).toEqual({shared:1, unchangedTimestamp:true, expired:2, mutated:3});
});

test("conversation configuration starts before a pending scope catalog finishes", async ({page}) => {
  await page.goto(fixtureUrl("overview"));
  await expect(page.locator("extended-openai-management-panel .dashboard-grid")).toBeVisible();
  const result = await page.evaluate(async () => {
    const host = browserHarness.panel;
    const original = host._hass.callWS;
    let releaseScope;
    let configStarted = false;
    host._hass.callWS = async message => {
      if (message.section === "scopes") await new Promise(resolve => { releaseScope = resolve; });
      if (message.section === "configuration") configStarted = true;
      return original(message);
    };
    const pending = host._navigate("data-memory", "conversations");
    while (!releaseScope) await new Promise(resolve => setTimeout(resolve, 0));
    const concurrent = configStarted;
    releaseScope();
    await pending;
    host._hass.callWS = original;
    return {concurrent, sessions:!!host._contentData?.sessions, config:!!host._configData};
  });
  expect(result).toEqual({concurrent:true, sessions:true, config:true});
});

test("late conversation configuration cannot replace a newer route result", async ({page}) => {
  await page.goto(fixtureUrl("overview"));
  await expect(page.locator("extended-openai-management-panel .dashboard-grid")).toBeVisible();
  const result = await page.evaluate(async () => {
    const host = browserHarness.panel;
    const original = host._hass.callWS;
    let release;
    host._hass.callWS = async message => {
      if (message.section === "configuration") await new Promise(resolve => { release = resolve; });
      return original(message);
    };
    const pending = host._navigate("data-memory", "conversations");
    while (!release) await new Promise(resolve => setTimeout(resolve, 0));
    await host._navigate("data-memory", "knowledge");
    const current = host._result;
    release();
    await pending;
    host._hass.callWS = original;
    return {same:host._result === current, view:host._viewKey(), draft:host._draft};
  });
  expect(result).toEqual({same:true, view:"data-memory/knowledge", draft:null});
});

test("voice and memory settings implementations load only when their routes are visited", async ({page}) => {
  const errors = trackPageErrors(page);
  const loaded = [];
  page.on("request", request => loaded.push(request.url()));
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  expect(loaded.some(url => url.endsWith("/voice-identity-ui.js"))).toBe(false);
  expect(loaded.some(url => url.endsWith("/memory-settings-ui.js"))).toBe(false);
  await panel.evaluate(host => host._navigate("assistant", "voice"));
  expect(loaded.some(url => url.endsWith("/voice-identity-ui.js"))).toBe(true);
  await expect(panel.locator(".voice-identity-flow")).toBeVisible();
  await panel.evaluate(host => host._navigate("data-memory", "memory-settings"));
  expect(loaded.some(url => url.endsWith("/memory-settings-ui.js"))).toBe(true);
  await expect(panel.locator("[data-memory-config]").first()).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("expired Knowledge list renders immediately and unchanged refresh preserves its DOM", async ({page}) => {
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-source")).toBeVisible();
  const result = await panel.evaluate(async host => {
    const key = host._sectionCacheKey();
    await host._navigate("overview");
    host._eocSectionCacheTimes.set(key, Date.now() - 31_000);
    const original = host._hass.callWS;
    let release;
    host._hass.callWS = async message => {
      if (message.section === "knowledge" && message.action === "list") await new Promise(resolve => { release = resolve; });
      return original(message);
    };
    const pending = host._navigate("data-memory", "knowledge");
    while (!release) await new Promise(resolve => setTimeout(resolve, 0));
    const button = host.shadowRoot.querySelector("#add-source");
    const immediate = !!button && !host._busy;
    release();
    await pending;
    host._hass.callWS = original;
    return {immediate, preserved:button === host.shadowRoot.querySelector("#add-source"), refreshed:host._eocSectionCacheTimes.get(key) > Date.now() - 1000};
  });
  expect(result).toEqual({immediate:true, preserved:true, refreshed:true});
});

test("overview uses the native registry and defers unrelated feature code", async ({page}) => {
  const errors = trackPageErrors(page);
  const modules = new Set();
  page.on("request", request => modules.add(new URL(request.url()).pathname.split("/").pop()));
  await page.addInitScript(() => {
    for (const name of ["define", "get", "whenDefined"]) {
      Object.defineProperty(customElements, name, {value:customElements[name], writable:false, configurable:false});
    }
  });
  await page.goto(fixtureUrl("overview") + "&predefine=1");
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  for (const name of ["agent-config-editor.js", "backup-transfer-ui.js", "exposed-attributes-ui.js", "management-provider-credentials.js", "usage-chart.js", "usage-input-footprint.js",
    "management-history-pagination.js", "quiet-hours-ui.js", "debug-management.js", "debug-panel.js", "management-function-repair.js"]) {
    expect(modules.has(name), name).toBe(false);
  }
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator("#prompt-preview-dialog")).toHaveCount(1);
  expect(modules.has("agent-config-editor.js")).toBe(true);
  await expect(panel.locator("#tool-dialog, #group-dialog, #restore-dialog")).toHaveCount(0);
  await panel.evaluate(host => host._navigate("usage-maintenance", "diagnostics"));
  expect(modules.has("management-provider-credentials.js")).toBe(true);
  await expect(panel.locator("#test-agent")).toBeVisible();
  await panel.evaluate(host => host._navigate("overview"));
  await expect(panel.locator("#prompt-preview-dialog, #tool-dialog, #restore-dialog, #rule-dialog")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});

test("Quiet Hours and request debugging initialize on first entry without global loader wrappers", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await panel.evaluate(host => {
    const original = host._hass.callWS;
    window.featureCalls = [];
    window.originalDataLoader = host._loadSectionData;
    host._hass.callWS = async message => {
      if (message.section === "quiet_hours") {
        window.featureCalls.push(message);
        return {config:message.config || {enabled:false, start:"22:00", end:"07:00", max_volume:0.3, wake_sound:"off"}, satellites:[]};
      }
      if (message.type.endsWith("/request_debug")) {
        window.featureCalls.push(message);
        return message.action === "agents" ? {agents:[host._selectedAgent()]} : {runs:[], enabled:false};
      }
      return original(message);
    };
  });
  await panel.evaluate(host => host._navigate("capabilities", "quiet-hours"));
  await expect(panel.locator("#qh-save")).toBeVisible();
  await expect(panel.locator(".qh-grid").first()).toHaveCSS("display", "grid");
  await panel.locator("#qh-enabled").check();
  await panel.locator("#qh-save").click();
  await expect.poll(() => page.evaluate(() => featureCalls.filter(c => c.section === "quiet_hours" && c.action === "update").length)).toBe(1);
  await panel.evaluate(host => host._navigate("usage-maintenance", "request-debug"));
  await expect(panel.locator("extended-openai-debug-panel[embedded]")).toBeVisible();
  await expect.poll(() => page.evaluate(() => featureCalls.filter(c => c.type.endsWith("/request_debug") && c.action === "runs").length)).toBe(1);
  await panel.evaluate(host => host._navigate("usage-maintenance", "usage"));
  await expect(panel.locator("#usage-window")).toBeVisible();
  await panel.evaluate(host => host._navigate("data-memory", "conversations"));
  expect(await panel.evaluate(host => host._loadSectionData === window.originalDataLoader)).toBe(true);
  await expectHarnessClean(page, errors);
});

test("a delayed first route import cannot start obsolete data work after navigation", async ({page}) => {
  let release;
  const requested = new Promise(resolve => {
    page.route("**/management-history-pagination.js", async route => {
      await new Promise(unblock => { release = unblock; resolve(); });
      await route.continue();
    });
  });
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await panel.evaluate(host => { window.pendingNavigation = host._navigate("data-memory", "conversations"); });
  await requested;
  await panel.evaluate(host => host._navigate("guide"));
  const before = await page.evaluate(() => browserHarness.calls.length);
  release();
  await page.evaluate(() => window.pendingNavigation);
  expect(await panel.evaluate(host => host._viewKey())).toBe("guide");
  expect(await page.evaluate(() => browserHarness.calls.length)).toBe(before);
  await expect(panel.locator("extended-openai-debug-panel, #archive-query")).toHaveCount(0);
});

test("repair-aware configuration requests work before the Function Tools editor is imported", async ({page}) => {
  const modules = new Set();
  page.on("request", request => modules.add(new URL(request.url()).pathname.split("/").pop()));
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  const calls = await panel.evaluate(async host => {
    host._selectedAgent().configuration_issue = {field:"functions", repairable:true};
    const original = host._hass.callWS;
    const repairCalls = [];
    host._hass.callWS = async message => {
      if (message.section === "function_repair") {
        repairCalls.push(message.action);
        return original({...message, section:"configuration", action:"get"});
      }
      return original(message);
    };
    await host._navigate("assistant", "basics");
    return repairCalls;
  });
  expect(calls).toEqual(["configuration_get"]);
  expect(modules.has("management-function-repair.js")).toBe(false);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
});
