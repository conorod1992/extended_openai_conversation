import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

const panelFor = page => page.locator("extended-openai-management-panel");
async function open(page, route, extra = "") {
  await page.goto(fixtureUrl(route, extra));
  const panel = panelFor(page);
  await expect(panel.locator("h1")).toBeVisible();
  return panel;
}

test("Guest interval survives details, dependent rendering, failure and newer in-flight edits", async ({page}) => {
  const panel = await open(page, "overview");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      if (request.section === "guest_mode" && request.action === "details") {
        await new Promise(resolve => { window.releaseGuestDetails = resolve; });
      }
      if (request.section === "guest_mode" && request.action === "update") {
        if (!window.guestRetried) { window.guestRetried = true; throw Object.assign(Error("Interval not saved"), {code:"invalid_request"}); }
        await new Promise(resolve => { window.releaseGuestUpdate = resolve; });
      }
      return original(request);
    };
  });
  await panel.getByRole("button", {name:"Capabilities", exact:true}).click();
  await panel.getByRole("button", {name:"Guest Mode", exact:true}).click();
  const start = panel.locator("#guest-start"), end = panel.locator("#guest-end");
  await start.fill("2030-11-02T18:00");
  await end.fill("2030-11-04T19:00");
  await panel.locator("#guest-indefinite").check();
  await page.evaluate(() => window.releaseGuestDetails());
  await expect(panel.getByText("Loading Guest capability details…", {exact:true})).toHaveCount(0);
  await panel.getByRole("combobox", {name:"Allow Knowledge Library reads", exact:true}).selectOption("on");
  await expect(start).toHaveValue("2030-11-02T18:00");
  await expect(end).toHaveValue("2030-11-04T19:00");
  await expect(panel.locator("#guest-indefinite")).toBeChecked();
  await panel.locator("#guest-update").click();
  await expect(panel.locator("#toast")).toContainText("Interval not saved");
  await expect(start).toHaveValue("2030-11-02T18:00");
  await panel.locator("#guest-update").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseGuestUpdate))).toBe(true);
  await start.fill("2030-11-03T20:00");
  await page.evaluate(() => window.releaseGuestUpdate());
  await expect(panel.getByText("Loading Guest capability details…", {exact:true})).toBeVisible();
  await page.evaluate(() => window.releaseGuestDetails());
  await expect(panel.locator("#toast")).toContainText("Guest Mode updated");
  await expect(start).toHaveValue("2030-11-03T20:00");
  await expect(end).toHaveValue("2030-11-04T19:00");
});

test("Guest interval cannot be submitted by a different authenticated owner", async ({page}) => {
  const panel = await open(page, "capabilities/guest-mode");
  await panel.evaluate(host => { host.hass = {...host.hass, user:{id:"interval-owner"}}; });
  await panel.locator("#guest-start").fill("2030-11-02T18:00");
  await panel.locator("#guest-end").fill("2030-11-04T19:00");
  await panel.evaluate(host => {
    window.intervalOwner = host.hass.user;
    host.hass = {...host.hass, user:{...host.hass.user, id:"another-administrator"}};
  });
  await panel.locator("#guest-update").click();
  await expect(panel.locator("#toast")).toContainText("Reload Guest Mode");
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "guest_mode" && call.action === "update").length)).toBe(0);
  await expect(panel.locator("#guest-start")).toHaveValue("2030-11-02T18:00");
  await panel.evaluate(host => { host.hass = {...host.hass, user:window.intervalOwner}; });
  await panel.locator("#guest-update").click();
  await expect(panel.locator("#toast")).toContainText("Guest Mode updated");
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "guest_mode" && call.action === "update").length)).toBe(1);
});

test("destructive retention is confirmed at shared Save; cancellation and changed submissions retain drafts", async ({page}) => {
  const panel = await open(page, "usage-maintenance/retention");
  await panel.locator('[data-retention-config="usage_request_retention_days"]').waitFor();
  await panel.evaluate(host => {
    host._result.options.usage_request_retention_days.unshift({value:0, label:"Disabled"});
    host._eocMainMarkup = null;
    host._render();
  });
  await panel.locator('[data-retention-config="usage_request_retention_days"]').selectOption("0");
  await panel.getByRole("button", {name:/^Assistant(?:,|$)/}).click();
  await panel.getByRole("button", {name:"Basics", exact:true}).click();
  await panel.getByRole("textbox", {name:"Agent name", exact:true}).fill("Assistant with draft");
  await expect(panel.locator(".save-bar")).toContainText("configuration draft, including edits in other sections");
  await panel.locator("#save-config").click();
  await expect(panel.locator("#confirm-dialog")).toBeVisible();
  await expect(panel.locator("#confirm-message")).toContainText("request details");
  await expect(panel.locator("#confirm-dialog")).toContainText("Jarvis");
  await panel.locator("#confirm-cancel").click();
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").length)).toBe(0);
  await expect(panel.getByRole("textbox", {name:"Agent name", exact:true})).toHaveValue("Assistant with draft");
  await panel.locator("#save-config").click();
  await panel.evaluate(host => { host._draftTitle = "Newer draft"; });
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#toast")).toContainText("Settings changed while confirmation");
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").length)).toBe(0);
  await panel.locator("#save-config").click();
  await panel.locator("#confirm-accept").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.usage_request_retention_days)).toBe(0);
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
  await panel.getByRole("textbox", {name:"Agent name", exact:true}).fill("Ordinary edit");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Ordinary edit");
  await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
});

test("configuration errors survive navigation and reveal collapsed fields without losing the shared draft", async ({page}) => {
  const panel = await open(page, "assistant/basics");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = request => request.section === "configuration" && request.action === "save" && request.config.max_tokens < 1
      ? Promise.resolve({valid:false, errors:{max_tokens:"Must be at least 1"}}) : original(request);
  });
  await panel.locator("#config-max_tokens").fill("-1");
  await panel.getByRole("button", {name:"Usage & Maintenance", exact:true}).click();
  await panel.getByRole("button", {name:"Usage data retention", exact:true}).click();
  await panel.locator("#save-config").click();
  const errors = panel.getByRole("region", {name:"Configuration errors", exact:true});
  await expect(errors).toContainText("Maximum response length: Must be at least 1");
  await errors.getByRole("button").click();
  await expect(panel.locator("#config-max_tokens")).toBeFocused();
  await expect(panel.locator("#config-max_tokens")).toHaveValue("-1");
  await expect(panel.locator("#config-max_tokens")).toHaveAttribute("aria-invalid", "true");
  const described = await panel.locator("#config-max_tokens").getAttribute("aria-describedby");
  expect(described).toContain("config-max_tokens-description");
  await panel.locator("#config-max_tokens").fill("1400");
  await expect(errors).toHaveCount(0);
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(1400);
  // An advanced field is mounted inside closed details; use the same navigation.
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = request => request.section === "configuration" && request.action === "save"
      ? Promise.resolve({valid:false, errors:{max_function_calls_per_conversation:"Review the limit"}}) : original(request);
  });
  await panel.locator(".general-advanced summary").click();
  await panel.locator("#config-max_function_calls_per_conversation").fill("-1");
  await panel.locator(".general-advanced summary").click();
  await panel.locator("#save-config").click();
  await errors.getByRole("button").click();
  await expect(panel.locator(".general-advanced")).toHaveAttribute("open", "");
  await expect(panel.locator("#config-max_function_calls_per_conversation")).toBeFocused();
});

test("debug status is unknown until loaded and retained snapshots are labelled after refresh failure", async ({page}) => {
  const panel = await open(page, "overview");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.debugStatusFails = true;
    host.hass.callWS = async request => {
      if (request.type.endsWith("/request_debug") && request.action === "runs") {
        if (!window.releaseDebugRead) await new Promise(resolve => { window.releaseDebugRead = resolve; });
        if (window.debugStatusFails) throw Error("Capture status read failed");
        return {enabled:true, limit:10, count:0, runs:[]};
      }
      return original(request);
    };
  });
  await panel.getByRole("button", {name:"Usage & Maintenance", exact:true}).click();
  await panel.getByRole("button", {name:"Request debugging", exact:true}).click();
  const debug = panel.locator("extended-openai-debug-panel");
  await expect(debug.locator("#enabled")).toBeDisabled();
  await expect(debug.locator("p.status")).toContainText("Loading capture status");
  await expect(debug).not.toContainText("Capture is off");
  await page.evaluate(() => window.releaseDebugRead());
  await expect(debug.locator(".error-box")).toContainText("Capture status read failed");
  await expect(debug.locator("#enabled")).toBeDisabled();
  await expect(debug).toContainText("Capture status unavailable");
  await page.evaluate(() => { window.debugStatusFails = false; });
  await debug.locator("#refresh").click();
  await expect(debug.locator("#enabled")).toBeChecked();
  await page.evaluate(() => { window.debugStatusFails = true; });
  await debug.locator("#refresh").click();
  await expect(debug.locator("p.status")).toContainText("Last known state (refresh failed)");
  await expect(debug.locator("#enabled")).toBeChecked();
  await expect(debug.locator("#enabled")).toBeDisabled();
});

test("failed Conversation search keeps the previous result identity and Retry publishes only actual matches", async ({page}) => {
  const panel = await open(page, "data-memory/conversations", "&seed_conversations=1");
  await expect(panel.locator(".view-session")).toHaveCount(1);
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      if (request.section === "conversations" && request.action === "search" && !window.historyRetried) {
        window.historyRetried = true;
        await new Promise(resolve => { window.releaseHistorySearch = resolve; });
        throw Error("Search unavailable");
      }
      return original(request);
    };
  });
  await panel.locator("#archive-query").fill("nothing-matches-this");
  await panel.locator("#archive-search").click();
  await expect(panel.locator(".eoc-history-pager")).toContainText("Loading; showing previous results");
  await page.evaluate(() => window.releaseHistorySearch());
  await expect(panel.locator(".eoc-history-pager")).toContainText("showing previous results");
  await expect(panel.locator(".eoc-history-pager")).not.toContainText("matching turns");
  await panel.locator(".eoc-history-pager").getByRole("button", {name:"Retry", exact:true}).click();
  await expect(panel.locator(".eoc-history-pager")).toContainText("0 of 0 matching turns");
  await expect(panel.locator(".view-session")).toHaveCount(0);
  await panel.getByRole("button", {name:"Clear search", exact:true}).click();
  await expect(panel.locator(".view-session")).toHaveCount(1);
  await expect(panel.locator(".eoc-history-pager")).not.toContainText("matching turns");
});

test("Memory remote search distinguishes pending, failed and authoritative empty results", async ({page}) => {
  const panel = await open(page, "data-memory/memories");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      if (request.section === "memories" && request.action === "search" && !window.memoryRetried) {
        window.memoryRetried = true;
        await new Promise(resolve => { window.releaseMemorySearch = resolve; });
        throw Error("Memory search offline");
      }
      if (request.section === "memories" && request.action === "search") return {memories:[], total:0, offset:0, returned:0, has_more:false};
      return original(request);
    };
  });
  await panel.locator("#list-search").fill("nothing-matches-this");
  await expect(panel.locator("[data-memory-search-status]")).toContainText("Searching all memories");
  await expect(panel.locator("[data-memory-empty]")).not.toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseMemorySearch))).toBe(true);
  await page.evaluate(() => window.releaseMemorySearch());
  await expect(panel.locator("[data-memory-search-status]")).toContainText("Search failed");
  await expect(panel.locator("[data-memory-empty]")).not.toBeVisible();
  await panel.getByRole("button", {name:"Retry search", exact:true}).click();
  await expect(panel.locator("[data-memory-empty]")).toBeVisible();
  await expect(panel.locator("[data-memory-empty]")).toContainText("No memories match");
  await panel.locator("#list-search").fill("");
  await expect(panel.locator("[data-memory-id]:visible")).toHaveCount(1);
});

test("collection edit Save returns focus to a surviving item or the collection action", async ({page}) => {
  const panel = await open(page, "data-memory/memories");
  await panel.locator(".memory-edit-button").click();
  await panel.locator("#memory-content").fill("Updated keyboard memory");
  await panel.locator("#memory-save").press("Enter");
  await expect(panel.locator(".memory-edit-button")).toBeFocused();
  await panel.getByRole("button", {name:"Knowledge Library", exact:true}).click();
  await panel.locator("#add-source").click();
  await panel.locator("#knowledge-title").fill("Keyboard source");
  await panel.locator("#knowledge-content").fill("Useful content");
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-dialog")).not.toBeVisible();
  await panel.locator("#list-search").fill("Keyboard");
  await panel.locator(".source-edit-button").click();
  await panel.locator("#knowledge-title").fill("Renamed source");
  await panel.locator("#knowledge-save").press("Enter");
  await expect(panel.locator("#add-source")).toBeFocused();
  await expect(panel.locator("[data-source-id]:visible")).toHaveCount(0);
});

test("attribute choices preserve keyboard focus and Close returns to Edit", async ({page}) => {
  await open(page, "overview");
  await page.evaluate(() => {
    const key = "extended-openai-browser-harness-state-v3";
    const state = JSON.parse(localStorage.getItem(key));
    state.configuration.config.exposed_entities_enabled = true;
    state.configuration.exposed_attribute_catalog = {entities:[{entity_id:"light.kitchen", reference:"registry:kitchen", name:"Kitchen", attributes:["brightness", "color_temp"], selected_attributes:[], durable_selection_available:true}], saved_unexposed:[]};
    localStorage.setItem(key, JSON.stringify(state));
  });
  const panel = await open(page, "assistant/prompt-context");
  await panel.locator("#exposed-entity-picker-fallback").selectOption("light.kitchen");
  const first = panel.getByRole("checkbox", {name:"brightness", exact:true});
  await first.press("Space");
  await expect(first).toBeFocused();
  await first.press("Tab");
  await expect(panel.getByRole("checkbox", {name:"color_temp", exact:true})).toBeFocused();
  await panel.locator("[data-close-exposed-editor]").click();
  await expect(panel.locator("[data-edit-exposed-entity]")).toBeFocused();
  await panel.locator("[data-edit-exposed-entity]").press("Enter");
  await expect(first).toBeFocused();
});

test("disabled Function Group editing preserves its availability, members and retryable draft", async ({page}) => {
  const panel = await open(page, "capabilities/functions");
  await panel.locator(".group-enabled").uncheck();
  const edit = panel.locator(".edit-group");
  await expect(edit).toBeEnabled();
  await edit.click();
  await panel.locator("#group-name").fill("Prepared while disabled");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    let failed = false;
    host.hass.callWS = request => {
      if (request.section === "tools" && request.action === "save_group" && !failed) { failed = true; return Promise.reject(Object.assign(Error("Group save rejected"), {code:"invalid_request"})); }
      return original(request);
    };
  });
  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-error")).toContainText("Group save rejected");
  await expect(panel.locator("#group-name")).toHaveValue("Prepared while disabled");
  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-dialog")).not.toBeVisible();
  const config = await page.evaluate(() => browserHarness.getState().configuration.config);
  expect(config.function_groups[0]).toMatchObject({enabled:false, name:"Prepared while disabled", functions:["baseline_tool"]});
  expect(config.functions[0].enabled).not.toBe(false);
  await expect(panel.locator(".group-enabled")).not.toBeChecked();
});

test("Usage unavailable dependencies do not become zero totals or reassuring empty failures", async ({page}) => {
  const panel = await open(page, "overview");
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    window.usageFails = true;
    host.hass.callWS = async request => {
      if (request.section === "usage" && window.usageFails) {
        if (request.action === "daily") throw Error("Daily data offline");
        if (request.action === "runs") { await new Promise(resolve => { window.releaseUsageRuns = resolve; }); throw Error("Run data offline"); }
      }
      return original(request);
    };
  });
  await panel.getByRole("button", {name:"Usage & Maintenance", exact:true}).click();
  await expect(panel.locator(".metric-grid").first()).toContainText("Daily usage unavailable");
  await expect(panel.locator(".metric-grid").first()).toContainText("9,999");
  await expect(panel.locator("[data-eoc-usage-diagnostics]")).not.toContainText("No failed runs were found");
  await expect(panel.locator("[data-eoc-usage-runs]")).toContainText("Loading recent runs");
  await page.evaluate(() => window.releaseUsageRuns());
  await expect(panel.locator("[data-eoc-usage-runs]")).toContainText("Recent runs unavailable");
  await expect(panel.locator("[data-eoc-usage-runs]")).not.toContainText("No saved recent runs");
  await page.evaluate(() => { window.usageFails = false; });
  await panel.getByRole("button", {name:"Retry usage loading", exact:true}).first().click();
  await expect(panel.locator("[data-eoc-usage-runs]")).toContainText("No saved recent runs");
  await expect(panel.locator("[data-eoc-usage-diagnostics]")).toContainText("No failed runs were found");
  await expect(panel.locator(".metric-grid").first()).not.toContainText("unavailable");
});
