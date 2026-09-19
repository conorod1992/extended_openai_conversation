import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

test("visiting lazy routes keeps the panel prototype fixed and feature imports lazy", async ({page}) => {
  const errors = trackPageErrors(page);
  const assets = [];
  page.on("request", (request) => assets.push(request.url()));
  await page.goto(fixtureUrl("guide"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".guide-search")).toBeVisible();
  for (const name of ["quiet-hours-ui.js", "management-function-repair.js", "usage-chart.js", "usage-input-footprint.js", "debug-panel.js", "management-provider-credentials.js"]) {
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

test("Usage retry and request details bind once after repeated explicit binding", async ({page}) => {
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
    panel._inputFootprintError = "Please retry";
    panel._result = {...panel._result,runs:{runs:[{run_id:"run-one",successful:false,error_type:"Example",request_count:1}]}};
    panel._render(); panel._render();
    const usage = getRouteFeature("usage-maintenance/usage");
    usage.bindUsageDiagnostics(panel); usage.bindUsageDiagnostics(panel);
    usage.bindInputFootprint(panel); usage.bindInputFootprint(panel);
  }, frontend);
  await panel.locator("#retry-input-footprint").click();
  await expect(panel.locator("#retry-input-footprint")).toHaveCount(0);
  expect(await page.evaluate(() => window.lazyUsageCalls.filter((call)=>call.section === "usage" && call.action === "footprint").length)).toBe(1);
  await panel.locator(".usage-run-details").click();
  await expect(panel.locator("#usage-request-dialog")).toHaveJSProperty("open",true);
  expect(await page.evaluate(() => window.lazyUsageCalls.filter((call)=>call.section === "usage" && call.action === "requests").length)).toBe(1);
  await page.keyboard.press("Escape");
  await expect(panel.locator("#usage-request-dialog")).toHaveJSProperty("open",false);
  await expectHarnessClean(page, errors);
});

test("credential observer is disposed on disconnect and restored on reconnect", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/diagnostics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#eoc-change-api-key")).toBeVisible();
  const result = await page.evaluate(async () => {
    const {panel} = window.browserHarness;
    const observer = panel._eocProviderCredentialObserver;
    const disconnect = observer.disconnect.bind(observer);
    let stopped = 0;
    observer.disconnect = () => { stopped++; disconnect(); };
    panel.remove();
    const cleared = panel._eocProviderCredentialObserver === null;
    document.body.append(panel);
    const restarted = panel._eocProviderCredentialObserver !== null && panel._eocProviderCredentialObserver !== observer;
    await panel._navigate("guide");
    return {stopped,cleared,restarted,afterNavigation:panel._eocProviderCredentialObserver === null};
  });
  expect(result).toEqual({stopped:1,cleared:true,restarted:true,afterNavigation:true});
  await expectHarnessClean(page, errors);
});
