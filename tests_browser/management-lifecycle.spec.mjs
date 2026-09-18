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

test("overview defers configuration editors and diagnostics until their routes are visited", async ({page}) => {
  const errors = trackPageErrors(page);
  const modules = new Set();
  page.on("request", request => modules.add(new URL(request.url()).pathname.split("/").pop()));
  await page.goto(fixtureUrl("overview") + "&predefine=1");
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  for (const name of ["agent-config-editor.js", "backup-transfer-ui.js", "exposed-attributes-ui.js", "management-provider-credentials.js"]) {
    expect(modules.has(name), name).toBe(false);
  }
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator("#prompt-preview-dialog")).toHaveCount(1);
  expect(modules.has("agent-config-editor.js")).toBe(true);
  await panel.evaluate(host => host._navigate("usage-maintenance", "diagnostics"));
  expect(modules.has("management-provider-credentials.js")).toBe(true);
  await expect(panel.locator("#test-agent")).toBeVisible();
  await panel.evaluate(host => host._navigate("overview"));
  await expect(panel.locator("#prompt-preview-dialog, #tool-dialog, #restore-dialog, #rule-dialog")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});
