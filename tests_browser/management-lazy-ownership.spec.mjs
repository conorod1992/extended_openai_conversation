import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

test("Home Assistant route paints its intro while configuration assets are still pending", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/agent-config-editor.js", async route => {
    requested = true;
    await gate;
    await route.continue();
  });

  await page.goto(fixtureUrl("capabilities/home-assistant"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => requested).toBe(true);
  await expect(panel.getByRole("heading", {name:"Home Assistant access", exact:true})).toBeVisible();
  await expect(panel.locator("main .loading")).toBeVisible();
  await expect(panel.getByRole("button", {name:"Configure exposed entity context"})).toHaveCount(0);

  release();
  await expect(panel.getByRole("button", {name:"Configure exposed entity context"})).toBeVisible();
  await expect(panel.locator("main .loading")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => {
    const names = new Set(performance.getEntriesByType("mark").map(entry => entry.name));
    return names.has("extended-openai:cold:route-title-present")
      && names.has("extended-openai:cold:route-title-next-frame");
  })).toBe(true);
  await expectHarnessClean(page, errors);
});

test("Assistant becomes usable while supplemental configuration guidance is pending", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/management-configuration-feature.js", async route => {
    requested = true;
    await gate;
    await route.continue();
  });

  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => requested).toBe(true);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await expect(panel.locator("main .loading")).toHaveCount(0);

  release();
  await expect.poll(() => page.evaluate(() =>
    performance.getEntriesByType("resource")
      .some(entry => entry.name.endsWith("/management-configuration-feature.js"))
  )).toBe(true);
  await expectHarnessClean(page, errors);
});

test("ordinary config routes do not load the Functions editor chunk", async ({page}) => {
  const errors = trackPageErrors(page);
  const assets = [];
  page.on("request", request => assets.push(new URL(request.url()).pathname.split("/").pop()));
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await panel.evaluate(host => host._navigate("usage-maintenance", "retention"));
  await expect(panel.locator('[data-config="usage_request_retention_days"]')).toBeVisible();
  await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
  await expect(panel.getByRole("heading", {name:"Export / Backup", exact:true})).toBeVisible();

  const beforeFunctions = assets.filter(file => file === "agent-config-tools.js" || file.startsWith("agent-config-tools-"));
  expect(beforeFunctions).toEqual([]);

  await panel.evaluate(host => host._navigate("capabilities", "functions"));
  await expect(panel.locator(".tools-surface")).toBeVisible();
  const afterFunctions = assets.filter(file => file === "agent-config-tools.js" || file.startsWith("agent-config-tools-"));
  expect(afterFunctions.length).toBeGreaterThan(0);
  await expectHarnessClean(page, errors);
});

test("Memory Settings becomes usable while configuration guidance is pending", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/management-configuration-feature.js", async route => {
    requested = true;
    await gate;
    await route.continue();
  });

  await page.goto(fixtureUrl("data-memory/memory-settings"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => requested).toBe(true);
  await expect(panel.locator('[data-memory-config="memory_mode"]')).toBeVisible();
  await expect(panel.locator("main .loading")).toHaveCount(0);

  release();
  await expect.poll(() => page.evaluate(() =>
    performance.getEntriesByType("resource")
      .some(entry => entry.name.endsWith("/management-configuration-feature.js"))
  )).toBe(true);
  await expectHarnessClean(page, errors);
});

test("cold Overview exposes stable shell, agent, asset, summary, and paint milestones", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  const required = [
    "extended-openai:cold:module-evaluated",
    "extended-openai:cold:constructed",
    "extended-openai:cold:connected",
    "extended-openai:cold:first-render-start",
    "extended-openai:cold:first-render-complete",
    "extended-openai:cold:shell-start",
    "extended-openai:cold:shell-complete",
    "extended-openai:cold:shell-title-present",
    "extended-openai:cold:shell-next-frame",
    "extended-openai:cold:agents-start",
    "extended-openai:cold:agents-complete",
    "extended-openai:cold:overview-asset-start",
    "extended-openai:cold:overview-asset-complete",
    "extended-openai:cold:overview-summary-start",
    "extended-openai:cold:overview-summary-complete",
    "extended-openai:cold:overview-content-present",
    "extended-openai:cold:overview-content-next-frame",
  ];
  await expect.poll(() => page.evaluate((expected) => {
    const names = new Set(performance.getEntriesByType("mark").map(entry => entry.name));
    return expected.every(name => names.has(name));
  }, required)).toBe(true);
  await expectHarnessClean(page, errors);
});

const lazyJourneys = [
  ["assistant", "basics", "management-configuration-feature", '[data-config="__title"]'],
  ["capabilities", "guest-mode", "management-guest-feature", "#guest-now"],
  ["data-memory", "knowledge", "management-knowledge-feature", "#add-source"],
  ["data-memory", "memories", "management-memory-feature", "#add-memory"],
  ["capabilities", "request-rules", "request-rules-ui", "#rule-search"],
  ["usage-maintenance", "usage", "usage-chart", "#usage-window"],
  ["capabilities", "functions", "agent-config-tools", ".tools-surface"],
];

for (const route of ["overview", "guide"]) {
  for (const bundled of [false, true]) {
    test(`cold ${route} keeps features unloaded and first visits cache them (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const assets = [];
      page.on("request", request => assets.push(new URL(request.url()).pathname.split("/").pop()));
      await page.coverage.startJSCoverage();
      await page.goto(fixtureUrl(route, bundled ? "&bundle=1" : ""));
      const panel = page.locator("extended-openai-management-panel");
      await expect(panel.locator(route === "guide" ? ".guide-search" : ".dashboard-grid")).toBeVisible();
      const coldCoverage = await page.coverage.stopJSCoverage();
      const absent = [
        "agent-config-editor", "agent-config-tools", "agent-config-model-presentation", "management-configuration-feature",
        "management-configuration-guidance", "management-configuration-clarity", "management-guest-feature",
        "guest-mode-ui", "management-knowledge-feature", "management-memory-feature", "keyed-collection", "management-temporary-memory", "request-rules-ui",
        "usage-chart", "management-feature-status", "backup-transfer-ui",
      ];
      for (const name of absent) {
        const matches = file => file === `${name}.js` || file.startsWith(`${name}-`);
        expect(assets.filter(matches), `${name} requested on ${route}`).toEqual([]);
        expect(coldCoverage.filter(entry => matches(entry.url.split("/").pop())), `${name} evaluated on ${route}`).toEqual([]);
      }
      for (const [destination, section, asset, control] of lazyJourneys) {
        await panel.evaluate((host, [p, s]) => host._navigate(p, s), [destination, section]);
        await expect(panel.locator(control)).toBeVisible();
        const loaded = assets.filter(file => file === `${asset}.js` || file.startsWith(`${asset}-`));
        expect(loaded.length, asset).toBeGreaterThan(0);
        await panel.evaluate(host => host._navigate("guide"));
        await panel.evaluate((host, [p, s]) => host._navigate(p, s), [destination, section]);
        await expect(panel.locator(control)).toBeVisible();
        expect(assets.filter(file => loaded.includes(file)), `${asset} requested again`).toEqual(loaded);
      }
      await expectHarnessClean(page, errors);
    });
  }
}

for (const [destination, section, asset, control, backend] of [
  ["data-memory", "knowledge", "management-knowledge-feature", "#add-source", "knowledge"],
  ["capabilities", "guest-mode", "management-guest-feature", "#guest-now", "guest_mode"],
  ["capabilities", "request-rules", "request-rules-ui", "#rule-search", "request_rules"],
  ["data-memory", "memories", "management-memory-feature", "#add-memory", "memories"],
  ["assistant", "basics", "agent-config-editor", '[data-config="__title"]', "configuration"],
]) {
  test(`${asset}: data starts during import and a stale completion cannot replace Guide`, async ({page}) => {
    const errors = trackPageErrors(page);
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    let requested = false;
    await page.route(`**/${asset}.js`, async route => { requested = true; await gate; await route.continue(); });
    await page.goto(fixtureUrl("guide"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".guide-search")).toBeVisible();
    await panel.evaluate((host, [p, s]) => { window.pendingLazyNavigation = host._navigate(p, s); }, [destination, section]);
    await expect.poll(() => requested).toBe(true);
    await expect.poll(() => page.evaluate(section => browserHarness.calls.some(call => call.section === section), backend)).toBe(true);
    await panel.evaluate(host => host._navigate("guide"));
    release();
    await page.evaluate(() => window.pendingLazyNavigation);
    await expect(panel.locator(".guide-search")).toBeVisible();
    await expect(panel.locator(control)).toHaveCount(0);
    await panel.evaluate((host, [p, s]) => host._navigate(p, s), [destination, section]);
    await expect(panel.locator(control)).toBeVisible();
    await expectHarnessClean(page, errors);
  });
}

test("a cold lazy import failure reaches the section error and a fresh load recovers", async ({page}) => {
  await page.route("**/management-guest-feature.js", route => route.abort());
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("main [role=alert]")).toContainText("Unable to load this frontend section");
  expect(await page.evaluate(() => browserHarness.rejections)).toEqual([]);
  await page.unroute("**/management-guest-feature.js");
  await page.goto(fixtureUrl("capabilities/guest-mode"));
  await expect(panel.locator("#guest-now")).toBeVisible();
});

for (const [routeName, asset, control] of [
  ["assistant/basics", "agent-config-editor", '[data-config="__title"]'],
  ["capabilities/functions", "agent-config-tools", ".tools-surface"],
  ["capabilities/request-rules", "request-rules-ui", "#rule-search"],
]) {
  test(`${asset}: failed feature load remains unavailable and a fresh page recovers`, async ({page}) => {
    await page.route(`**/${asset}.js`, route => route.abort());
    await page.goto(fixtureUrl(routeName));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("main [role=alert]")).toContainText("Unable to load this frontend section");
    await expect(panel.locator(control)).toHaveCount(0);
    expect(await page.evaluate(() => browserHarness.rejections)).toEqual([]);
    await page.unroute(`**/${asset}.js`);
    await page.goto(fixtureUrl(routeName));
    await expect(panel.locator(control)).toBeVisible();
  });
}

test("agent changes and reconnects during a shared lazy import keep the newest data", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let requests = 0;
  await page.route("**/management-guest-feature.js", async route => { requests++; await gate; await route.continue(); });
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".guide-search")).toBeVisible();
  await panel.evaluate(host => {
    host._data.agents.push({...host._selectedAgent(), subentry_id:"second", title:"Second assistant"});
    const original = host._hass.callWS.bind(host._hass);
    host._hass.callWS = async message => {
      const result = await original(message);
      if (message.section === "guest_mode" && message.action === "get") {
        return {...result, config:{...result.config, guest_mode_enabled:message.subentry_id === "second"}};
      }
      return result;
    };
    window.firstGuestNavigation = host._navigate("capabilities", "guest-mode");
  });
  await expect.poll(() => requests).toBe(1);
  await panel.locator("#agent").selectOption("second");
  await expect.poll(() => page.evaluate(() => browserHarness.calls.some(call => call.section === "guest_mode" && call.subentry_id === "second"))).toBe(true);
  await panel.evaluate(host => { host.remove(); document.body.append(host); });
  release();
  await page.evaluate(() => window.firstGuestNavigation);
  await expect(panel.locator("#guest-controls-enabled")).toBeChecked();
  await expect(panel.locator("#agent")).toHaveValue("second");
  expect(requests).toBe(1);
  await expectHarnessClean(page, errors);
});

test("search into a cold configuration route retains focus until its assets arrive", async ({page}) => {
  const errors = trackPageErrors(page);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let requested = false;
  await page.route("**/management-configuration-feature.js", async route => { requested = true; await gate; await route.continue(); });
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#settings-search").pressSequentially("assistant name");
  await panel.locator(".settings-result").first().click();
  await expect.poll(() => requested).toBe(true);
  release();
  await expect(panel.locator('[data-config="__title"]')).toBeFocused();
  await page.goBack();
  await expect(panel.locator(".guide-search")).toBeVisible();
  await page.goForward();
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("visiting lazy routes keeps the panel prototype fixed and feature imports lazy", async ({page}) => {
  const errors = trackPageErrors(page);
  const assets = [];
  page.on("request", (request) => assets.push(request.url()));
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".guide-search")).toBeVisible();
  for (const name of ["quiet-hours-ui.js", "management-function-repair.js", "agent-config-tools.js", "usage-chart.js", "debug-panel.js", "management-provider-credentials.js"]) {
    expect(assets.some((url) => url.endsWith(`/${name}`)), name).toBe(false);
  }
  const unchanged = await page.evaluate(async () => {
    const {panel} = window.browserHarness;
    const prototype = Object.getPrototypeOf(panel);
    const before = Object.getOwnPropertyDescriptors(prototype);
    Object.freeze(prototype);
    for (const [page, section] of [
      ["capabilities", "quiet-hours"], ["capabilities", "functions"],
      ["usage-maintenance", "usage"], ["usage-maintenance", "diagnostics"],
      ["usage-maintenance", "request-debug"], ["usage-maintenance", "usage"],
    ]) {
      await panel._navigate(page, section);
      panel._render(); panel._render();
    }
    return Object.keys(before).every((key) => {
      const after = Object.getOwnPropertyDescriptor(prototype, key);
      return after.value === before[key].value && after.get === before[key].get && after.set === before[key].set;
    });
  });
  expect(unchanged).toBe(true);
  await expect(panel.getByRole("heading", {name:"Usage period",exact:true})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("embedded Debug pins its assistant and binds paged view/copy once", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("guide"));
  await expect(page.locator("extended-openai-management-panel .guide-search")).toBeVisible();
  await page.evaluate(async () => {
    const {panel,hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    const agent = panel._selectedAgent();
    localStorage.setItem("extended-openai-debug-agent", "other-agent");
    window.lazyDebugCalls = [];
    hass.callWS = async (message) => {
      if (message.type !== "extended_openai_conversation_responses/request_debug") return original(message);
      window.lazyDebugCalls.push(message);
      if (message.action === "agents") return {agents:[agent,{...agent,subentry_id:"other-agent",title:"Other"}]};
      if (message.action === "runs") return {enabled:true,limit:10,count:1,runs:[{debug_id:"debug-one",successful:true,continuity_mode:"device",resolved_conversation_id:"conversation-one"}]};
      if (message.action === "get") return {trace:{debug_id:"debug-one",page:message.provider_offset,provider_requests:[{number:message.provider_offset}],management_projection:{provider_requests:{offset:message.provider_offset,limit:5,returned:5,total:10,has_more:message.provider_offset === 0,next_offset:5}}}};
      return {};
    };
    await panel._navigate("usage-maintenance","request-debug");
  });
  const debug = page.locator("extended-openai-debug-panel");
  await expect(debug.locator('[data-view="debug-one"]')).toBeVisible();
  await expect(debug.locator("#agent")).not.toBeVisible();
  const runs = await page.evaluate(() => window.lazyDebugCalls.filter((call)=>call.action === "runs"));
  expect(runs).toHaveLength(1);
  expect(runs[0].subentry_id).not.toBe("other-agent");
  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    panel._render(); panel._render();
    const debug = panel.shadowRoot.querySelector("extended-openai-debug-panel");
    window.lazyDebugCopies = [];
    debug._copyText = async (text) => { window.lazyDebugCopies.push(text); };
  });
  await debug.locator('[data-view="debug-one"]').click();
  await expect(debug.locator("#debug-provider-status")).toHaveText("Provider requests 1–5 of 10");
  await debug.locator("#debug-provider-next").click();
  await expect(debug.locator("#debug-provider-status")).toHaveText("Provider requests 6–10 of 10");
  await debug.locator("#copy-debug-log").click();
  await expect.poll(() => page.evaluate(() => window.lazyDebugCopies.length)).toBe(1);
  expect(await page.evaluate(() => JSON.parse(window.lazyDebugCopies[0]).page)).toBe(5);
  expect(await page.evaluate(() => window.lazyDebugCalls.filter((call)=>call.action === "get").map((call)=>call.provider_offset))).toEqual([0,5]);
  await expectHarnessClean(page, errors);
});

test("Usage request details bind once after repeated explicit binding", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/usage"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#usage-window")).toBeVisible();
  await page.evaluate(async (base) => {
    const {getRouteFeature} = await import(`${base}management-route.js`);
    const {panel,hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.lazyUsageCalls = [];
    hass.callWS = async (message) => { window.lazyUsageCalls.push(message); return original(message); };
    panel._result = {...panel._result,runs:{runs:[{run_id:"run-one",successful:false,error_type:"Example",request_count:1}]}};
    panel._render(); panel._render();
    const usage = getRouteFeature("usage-maintenance/usage");
    usage.bindUsageDiagnostics(panel); usage.bindUsageDiagnostics(panel);
  }, frontend);
  await panel.locator(".usage-run-details").click();
  await expect(panel.locator("#usage-request-dialog")).toHaveJSProperty("open",true);
  expect(await page.evaluate(() => window.lazyUsageCalls.filter((call)=>call.section === "usage" && call.action === "requests").length)).toBe(1);
  await page.keyboard.press("Escape");
  await expect(panel.locator("#usage-request-dialog")).toHaveJSProperty("open",false);
  await expectHarnessClean(page, errors);
});

test("credential diagnostics result handler is disposed on disconnect and restored on reconnect", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#eoc-change-api-key")).toBeVisible();
  const result = await page.evaluate(async () => {
    const {panel} = window.browserHarness;
    const handler = panel._eocProviderCredentialResultHandler;
    let removed = 0;
    const originalRemove = panel.shadowRoot.removeEventListener.bind(panel.shadowRoot);
    panel.shadowRoot.removeEventListener = (type, listener, options) => {
      if (type === "eoc-diagnostics-result" && listener === handler) removed++;
      return originalRemove(type, listener, options);
    };
    panel.remove();
    const cleared = panel._eocProviderCredentialResultHandler === null;
    document.body.append(panel);
    const restarted = panel._eocProviderCredentialResultHandler !== null
      && panel._eocProviderCredentialResultHandler !== handler;
    await panel._navigate("guide");
    return {removed,cleared,restarted,afterNavigation:panel._eocProviderCredentialResultHandler === null};
  });
  expect(result).toEqual({removed:1,cleared:true,restarted:true,afterNavigation:true});
  await expectHarnessClean(page, errors);
});
