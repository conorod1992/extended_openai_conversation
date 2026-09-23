import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

test("search debounces only results and preserves input, focus, page and styles", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();
  await page.evaluate(() => {
    const {panel} = window.browserHarness;
    const root = panel.shadowRoot;
    window.renderProbe = {input:root.querySelector("#settings-search"), main:root.querySelector("main").firstElementChild, style:root.querySelector("[data-eoc-persistent-styles]"), renders:0, mutations:[]};
    const render = panel._render.bind(panel);
    panel._render = (...args) => { window.renderProbe.renders += 1; return render(...args); };
    const observer = new MutationObserver((records) => window.renderProbe.mutations.push(...records));
    observer.observe(root.querySelector("main"), {childList:true, subtree:true});
    window.renderProbe.observer = observer;
  });
  const input = panel.locator("#settings-search");
  await input.focus();
  const immediate = await page.evaluate(() => {
    const {input} = window.renderProbe;
    input.value = "timeout";
    input.setSelectionRange(3, 3);
    input.dispatchEvent(new InputEvent("input", {bubbles:true, composed:true}));
    return {renders:window.renderProbe.renders, results:window.browserHarness.panel.shadowRoot.querySelectorAll(".settings-result").length};
  });
  expect(immediate).toEqual({renders:0, results:0});
  await expect(panel.locator(".settings-result").first()).toContainText("Conversation timeout");
  const result = await page.evaluate(() => {
    const probe = window.renderProbe;
    const root = window.browserHarness.panel.shadowRoot;
    probe.observer.disconnect();
    return {renders:probe.renders, mutations:probe.mutations.length, input:root.querySelector("#settings-search") === probe.input, main:root.querySelector("main").firstElementChild === probe.main, style:root.querySelector("[data-eoc-persistent-styles]") === probe.style, focused:root.activeElement === probe.input, caret:probe.input.selectionStart};
  });
  expect(result).toEqual({renders:0, mutations:0, input:true, main:true, style:true, focused:true, caret:3});
  await expectHarnessClean(page, errors);
});

test("unchanged renders retain toolbar, navigation, guidance and main nodes", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const result = await page.evaluate(async () => {
    const {panel} = window.browserHarness;
    panel._result.configuration_guidance = {effective_api_mode:"responses"};
    panel._draft.api_mode = "auto";
    panel._render();
    await new Promise(requestAnimationFrame);
    const root = panel.shadowRoot;
    const selectors = ["main > :first-child", "#settings-search", ".eoc-agent-context-row", ".subsection-nav", "[data-eoc-guidance-generated]", "[data-eoc-persistent-styles]"];
    const before = selectors.map((selector) => root.querySelector(selector));
    const observer = new MutationObserver(() => {});
    observer.observe(root, {childList:true, subtree:true});
    panel._render();
    panel._render();
    const records = observer.takeRecords();
    observer.disconnect();
    return {stable:selectors.map((selector, index) => Boolean(before[index]) && root.querySelector(selector) === before[index]), elements:records.reduce((sum, record) => sum + [...record.addedNodes, ...record.removedNodes].filter((node) => node.nodeType === Node.ELEMENT_NODE).length, 0), records:records.length};
  });
  console.log("Unchanged render mutations:", result);
  expect(result.stable.every(Boolean)).toBe(true);
  expect(result.elements).toBe(0);
  expect(result.records).toBe(0);
});

test("unrelated routes omit feature editors; page changes still refresh and bind controls", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const result = await page.evaluate(async (base) => {
    const {renderManagement} = await import(`${base}management-renderer.js`);
    const {panel} = window.browserHarness;
    const root = panel.shadowRoot;
    renderManagement(panel);
    const main = root.querySelector("main").firstElementChild;
    renderManagement(panel);
    const dialogOnly = {mainStable:root.querySelector("main").firstElementChild === main, featureEditors:root.querySelectorAll("#knowledge-dialog,#memory-dialog,#temporary-memory-dialog,#session-dialog,#reassign-dialog").length, confirmation:Boolean(root.querySelector("#confirm-dialog"))};
    panel._error = "Page refresh regression marker";
    renderManagement(panel);
    const pageUpdated = root.querySelector("main").textContent.includes(panel._error);
    panel._error = null;
    renderManagement(panel);
    return {dialogOnly, pageUpdated, restored:Boolean(root.querySelector('[data-config="chat_model"]'))};
  }, frontend);
  expect(result).toEqual({dialogOnly:{mainStable:true, featureEditors:0, confirmation:true}, pageUpdated:true, restored:true});
  await page.locator('extended-openai-management-panel [data-config="__title"]').fill("Changed title");
  await expect(page.locator("extended-openai-management-panel .save-bar")).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("search configuration retry updates results without rendering or stealing focus", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".dashboard-grid")).toBeVisible();
  await page.evaluate(() => {
    const {panel} = window.browserHarness;
    const call = panel._call.bind(panel);
    const render = panel._render.bind(panel);
    window.searchCalls = {gets:0, renders:0, input:panel.shadowRoot.querySelector("#settings-search")};
    panel._render = (...args) => { window.searchCalls.renders += 1; return render(...args); };
    panel._call = async (section, action, ...args) => {
      if (section === "configuration" && action === "get" && ++window.searchCalls.gets === 1) return Promise.reject(new Error("Transient search fixture failure"));
      const result = await call(section, action, ...args);
      if (section === "configuration" && action === "get") result.config.conversation_timeout_minutes = 30;
      return result;
    };
  });
  await panel.locator("#settings-search").fill("conversation timeout");
  await expect(panel.locator("#settings-search-retry")).toBeVisible();
  await panel.locator("#settings-search-retry").click();
  await expect(panel.locator(".settings-current").first()).toContainText("Current:");
  expect(await page.evaluate(() => ({gets:window.searchCalls.gets,renders:window.searchCalls.renders,stable:window.searchCalls.input === window.browserHarness.panel.shadowRoot.querySelector("#settings-search")}))).toEqual({gets:2,renders:0,stable:true});
  await panel.locator(".settings-result").first().click();
  await expect(panel.locator('[data-config="conversation_continuity"]')).toBeVisible();
  await expect(panel.locator("#settings-search")).toHaveValue("");
  await expectHarnessClean(page, errors);
});

test("capability configuration routes share the editor markup cache", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/web-skills"));
  await expect(page.locator('extended-openai-management-panel [data-config="web_search"]')).toBeVisible();
  const result = await page.evaluate(() => {
    const {panel} = window.browserHarness;
    panel._content(panel._selectedAgent());
    const escape = panel._e.bind(panel);
    let calls = 0;
    panel._e = (value) => { calls += 1; return escape(value); };
    const first = panel._content(panel._selectedAgent());
    const cachedCalls = calls;
    panel._configDirty = true;
    panel._draft.web_search = !panel._draft.web_search;
    const changed = panel._content(panel._selectedAgent());
    return {cachedCalls, dirtyCalls:calls, changed:first !== changed};
  });
  expect(result.cachedCalls).toBe(0);
  expect(result.dirtyCalls).toBeGreaterThan(0);
  expect(result.changed).toBe(true);
});

test("same-page search navigation focuses the setting without rebuilding the page", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible();
  await page.evaluate(() => { window.originalTitle = window.browserHarness.panel.shadowRoot.querySelector('[data-config="__title"]'); });
  await panel.locator("#settings-search").pressSequentially("assistant name");
  await panel.locator(".settings-result").filter({hasText:"Agent name"}).click();
  await expect(title).toBeFocused();
  expect(await title.evaluate((node) => node === window.originalTitle)).toBe(true);
});
