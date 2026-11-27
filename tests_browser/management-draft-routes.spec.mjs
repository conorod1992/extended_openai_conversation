import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const action of ["save-config", "revert-config"]) {
  test(`shared Assistant draft can ${action} from Memories without replacing its view`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
    const panel = page.locator("extended-openai-management-panel");
    const title = panel.locator('[data-config="__title"]');
    await title.fill("Draft assistant name");
    await panel.evaluate(host => host._navigate("data-memory", "memories"));
    await expect(panel.locator("#assistant-dirty-state")).toBeVisible();
    const search = panel.locator("#list-search");
    await search.fill("kept in the current view");
    await panel.evaluate(host => {
      window.draftRouteMain = host.shadowRoot.querySelector("main");
      window.draftRouteSearch = host.shadowRoot.querySelector("#list-search");
      window.draftRouteResult = host._result;
      window.draftRouteWrites = browserHarness.calls.length;
    });
    await panel.locator(`#${action}`).click();
    await expect(panel.locator("#assistant-dirty-state")).toHaveCount(0);
    await expect(search).toHaveValue("kept in the current view");
    const state = await panel.evaluate(host => ({
      sameMain: window.draftRouteMain === host.shadowRoot.querySelector("main"),
      sameSearch: window.draftRouteSearch === host.shadowRoot.querySelector("#list-search"),
      sameResult: window.draftRouteResult === host._result,
      saves: browserHarness.calls.slice(window.draftRouteWrites).filter(call => call.section === "configuration" && call.action === "save").length,
    }));
    expect(state).toEqual({sameMain:true, sameSearch:true, sameResult:true, saves:Number(action === "save-config")});
    await expect(panel.locator("#agent option:checked"))
      .toHaveText(action === "save-config" ? "Draft assistant name" : "Jarvis");
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await expect(title).toHaveValue(action === "save-config" ? "Draft assistant name" : "Jarvis");
    await expectHarnessClean(page, errors);
  });
}

test("shared and page drafts expose distinct actions and remain independent", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Renamed assistant");
  await panel.evaluate(host => host._navigate("capabilities", "quiet-hours"));
  const enabled = panel.locator("#qh-enabled");
  await expect(enabled).toBeVisible();
  const desired = !await enabled.isChecked();
  await enabled.setChecked(desired);
  await expect(panel.getByRole("button", {name:"Discard Assistant settings", exact:true})).toBeVisible();
  await expect(panel.getByRole("button", {name:"Discard changes", exact:true})).toBeVisible();
  await panel.getByRole("button", {name:"Save Assistant settings", exact:true}).click();
  await expect(panel.locator("#agent option:checked")).toHaveText("Renamed assistant");
  await expect(panel.getByRole("button", {name:"Save changes", exact:true})).toBeEnabled();
  expect(await enabled.isChecked()).toBe(desired);
  await panel.getByRole("button", {name:"Save changes", exact:true}).click();
  await expect(panel.locator("#save-page")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});

for (const stale of [false, true]) test(`shared Assistant save ${stale ? "keeps a genuine Guest policy conflict" : "advances the matching Guest policy revision"}`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Renamed Guest assistant");
  await panel.evaluate((host, staleRevision) => {
    const call = host.hass.callWS.bind(host.hass);
    let revision = browserHarness.getState().configuration.revision;
    host.hass.callWS = async request => {
      if (request.section === "guest_mode" && request.action === "save_policy") {
        if (request.revision !== revision) throw {code:"eoai_mutation_rejected", message:"Configuration changed in another tab"};
        return call({...request, revision:browserHarness.getState().guest.revision});
      }
      const response = await call(request);
      if (request.section === "guest_mode" && request.action === "get") {
        return {...response, revision:staleRevision ? "older-guest-revision" : revision};
      }
      if (request.section === "configuration" && request.action === "save") revision = response.revision;
      return response;
    };
    return host._navigate("capabilities", "guest-mode");
  }, stale);
  const web = panel.locator("#guest-web-search");
  await web.evaluate(input => {
    for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true;
  });
  await web.check();
  await panel.getByRole("button", {name:"Save Assistant settings", exact:true}).click();
  await expect(panel.locator("#agent option:checked")).toHaveText("Renamed Guest assistant");
  await expect(web).toBeChecked();
  await panel.locator("#save-page").click();
  if (stale) {
    await expect(panel.locator("#toast")).toContainText("Configuration changed in another tab");
    await expect(panel.locator("#save-page")).toBeEnabled();
    await expect(web).toBeChecked();
    expect(await page.evaluate(() => browserHarness.getState().guest.config.guest_web_search)).toBe(false);
  } else {
    await expect(panel.locator("#save-page")).toHaveCount(0);
    expect(await page.evaluate(() => browserHarness.getState().guest.config.guest_web_search)).toBe(true);
  }
  await expectHarnessClean(page, errors);
});

test("a shared save refreshes authoritative Overview health without replacing Broadcast", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Renamed overview assistant");
  const model = panel.locator('[data-config="chat_model"]');
  await model.fill("gpt-4o");
  await model.press("Tab");
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    Object.assign(host._draft, {prompt:"", memory_mode:"off", archive_retention_days:7, web_search:true});
    host._syncConfigDirty();
    host.hass.callWS = async request => {
      const response = await call(request);
      if (request.section !== "overview" || request.action !== "primary") return response;
      const config = browserHarness.getState().configuration.config;
      if (config.chat_model === "gpt-4o") await new Promise(resolve => { window.releaseOverviewPrimary = resolve; });
      return {...response, conversations:{...response.conversations, archive_retention_days:config.archive_retention_days || 30},
        setup_health:{...response.setup_health,
          model_lifecycle:config.chat_model === "gpt-5-mini" ? {status:"deprecated", model:"gpt-5-mini"} : {},
          prompt_state:config.prompt === "" ? "empty" : "custom",
          memory:{...response.setup_health.memory, mode:config.memory_mode || "manual"},
          web_search:{enabled:config.web_search === true, available:true, effective_api_mode:"responses"},
        },
      };
    };
    return host._navigate("overview");
  });
  await expect(panel.locator('[data-health-check="model_lifecycle"]')).toHaveCount(1);
  await expect(panel.locator("#broadcast-enabled")).toBeVisible();
  await panel.locator("#broadcast-enabled").check();
  const composer = panel.locator("#broadcast-message");
  await composer.fill("Keep this unsent announcement");
  const broadcast = await composer.elementHandle();
  await panel.locator("#save-config").click();
  await expect(panel.locator("#toast")).toContainText("Changes saved");
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseOverviewPrimary))).toBe(true);
  await expect(panel.locator("main h1")).toHaveText("Renamed overview assistant");
  await expect(panel.locator("#agent option:checked")).toHaveText("Renamed overview assistant");
  await expect(panel.locator(".agent-picker small")).toContainText("gpt-4o");
  await composer.evaluate(node => { node.focus(); node.setSelectionRange(4, 4); });
  await page.evaluate(() => window.releaseOverviewPrimary());
  await expect(panel.locator(".dashboard-card").filter({has:page.getByRole("heading", {name:"Assistant", exact:true})}).locator("strong"))
    .toContainText("gpt-4o");
  await expect(panel.locator("#eoc-getting-started")).toContainText("gpt-4o");
  await expect(panel.locator('[data-health-check="provider_runtime"] strong')).toContainText("gpt-4o");
  await expect(panel.locator('[data-health-check="model_lifecycle"]')).toHaveCount(0);
  await expect(panel.locator('[data-health-check="instructions"] strong')).toHaveText("Empty");
  await expect(panel.locator('[data-health-check="memory"] strong')).toHaveText("Off by choice");
  await expect(panel.locator('[data-health-check="web_search"] strong')).toHaveText("Available");
  await expect(panel.getByText("Retention: 7 days", {exact:true})).toBeVisible();
  await expect(composer).toHaveValue("Keep this unsent announcement");
  await expect(composer).toBeFocused();
  expect(await composer.evaluate(node => node.selectionStart)).toBe(4);
  expect(await broadcast.evaluate(node => node.isConnected)).toBe(true);
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "overview" && call.action === "primary").length)).toBe(2);
  await expectHarnessClean(page, errors);
});

test("saving reduced retention refreshes settled Usage and evicts other cached windows", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/retention", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="usage_run_retention_days"]').selectOption("0");
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async request => {
      const response = await call(request);
      if (request.section !== "usage") return response;
      const retention = browserHarness.getState().configuration.config.usage_run_retention_days;
      const run = {run_id:"retained-before-save", total_tokens:42, completed_at:new Date().toISOString(), request_count:1, successful:false, error_type:"Outside retention"};
      if (request.action === "runs") return {runs:retention ? [run] : []};
      if (request.action === "summary") {
        if (!retention) await new Promise(resolve => { window.releaseUsageSummary = resolve; });
        return {...response, latest:retention ? run : null};
      }
      if (request.action === "daily") return {...response, local_run_summary:{count:retention ? 1 : 0, start_date:request.start_date, end_date:request.end_date}};
      if (request.action === "retention") return {usage_run_retention_days:retention};
      return response;
    };
    const staleKey = `${host._agentId}|usage-maintenance/usage|all-days`;
    host._sectionCache.set(staleKey, {summary:{stale:true}});
    host._eocSectionCacheTimes.set(staleKey, Date.now());
    host._usageHistoryWindow = "90";
    return host._navigate("usage-maintenance", "usage");
  });
  const recent = panel.locator("[data-eoc-usage-runs]");
  const retained = panel.locator(".usage-fact").filter({hasText:"Retained runs without provider requests"}).locator("strong");
  await expect(recent).toContainText("Outside retention");
  await expect(retained).toHaveText("1");
  await expect.poll(() => panel.evaluate(host => host._result?.loading?.retention)).toBe(false);
  await panel.evaluate(host => {
    const key = `${host._agentId}|usage-maintenance/usage|year`;
    host._sectionCache.set(key, {stale:true});
    host._eocSectionCacheTimes.set(key, Date.now());
  });
  await panel.getByRole("button", {name:"Save Assistant settings", exact:true}).click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#toast")).toContainText("Changes saved");
  await expect.poll(() => page.evaluate(() => Boolean(window.releaseUsageSummary))).toBe(true);
  const windowSelector = panel.getByLabel("History window");
  await expect(windowSelector).toHaveValue("90");
  await windowSelector.selectOption("all");
  await expect(retained).toHaveText("0");
  await page.evaluate(() => window.releaseUsageSummary());
  await expect(recent).toContainText("No saved recent runs.");
  await expect(retained).toHaveText("0");
  await expect(windowSelector).toHaveValue("all");
  await expect.poll(() => panel.evaluate(host => [host._result?.summary?.latest, host._result?.retention?.usage_run_retention_days])).toEqual([null, 0]);
  expect(await panel.evaluate(host => host._result?.days?.requested_window)).toBe("all");
  expect(await panel.evaluate(host => host._sectionCache.has(`${host._agentId}|usage-maintenance/usage|year`))).toBe(false);
  await expectHarnessClean(page, errors);
});

test("Assistant validation summary follows the save bar and links to its setting", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await title.fill("Rejected title");
  await panel.evaluate(host => {
    const callWS = host.hass.callWS.bind(host.hass);
    host.hass.callWS = request => request.section === "configuration" && request.action === "save"
      ? Promise.resolve({valid:false, errors:{title:"Choose a different name"}}) : callWS(request);
    return host._navigate("data-memory", "memories");
  });
  await panel.locator("#save-config").click();
  await expect(panel.locator("#eoc-shared-configuration-bar #configuration-errors")).toContainText("Choose a different name");
  await panel.locator("#configuration-errors [data-config-error]").click();
  await expect(title).toBeFocused();
  await expect(panel.locator("main #configuration-errors")).toContainText("Choose a different name");
  await panel.evaluate(host => host._navigate("data-memory", "memories"));
  await expect(panel.locator("#eoc-shared-configuration-bar #configuration-errors")).toContainText("Choose a different name");
  await expect(panel.locator("#configuration-errors")).toHaveCount(1);
  await expectHarnessClean(page, errors);
});

for (const [view, section, actions] of [
  ["capabilities/guest-mode", "guest_mode", ["details"]],
  ["usage-maintenance/usage", "usage", ["runs", "retention"]],
  ["data-memory/conversations", "conversations", ["active"]],
  ["overview", "overview", ["detail"]],
]) {
  test(`Assistant save resumes pending secondary reads for ${view}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator('[data-config="__title"]').fill("Assistant with pending reads");
    await panel.evaluate((host, {view, section, actions}) => {
      const callWS = host.hass.callWS.bind(host.hass);
      window.secondaryReads = Object.fromEntries(actions.map(action => [action, []]));
      host.hass.callWS = async request => {
        const response = await callWS(request);
        if (request.section !== section || !actions.includes(request.action)
            || (section === "overview" && request.kind !== "usage")) return response;
        const holds = window.secondaryReads[request.action];
        const witness = holds.length + 1;
        const marked = section === "overview"
          ? {...response, usage:{...response.usage, witness}} : {...response, witness};
        return new Promise(resolve => holds.push(() => resolve(marked)));
      };
      return host._navigate(...view.split("/"));
    }, {view, section, actions});
    const requests = () => page.evaluate(() => Object.values(window.secondaryReads).map(holds => holds.length));
    await expect.poll(requests).toEqual(actions.map(() => 1));
    if (section === "conversations") {
      await expect(panel.locator("main #save-config")).toBeEnabled();
      await expect(panel.locator("#eoc-shared-configuration-bar #save-config")).toHaveCount(0);
    }
    await panel.locator("#save-config").click();
    await expect.poll(requests).toEqual(actions.map(() => 2));
    await page.evaluate(() => Object.values(window.secondaryReads).forEach(holds => holds[1]()));
    const published = () => panel.evaluate((host, {section, actions}) => {
      if (section === "guest_mode") return [host._result?.witness, host._result?.loading?.details];
      if (section === "overview") return [host._result?.usage?.witness, host._result?.loading?.usage];
      if (section === "conversations") return [host._contentData?.active?.witness, host._contentData?.loading?.active];
      return actions.flatMap(action => [host._result?.[action]?.witness, host._result?.loading?.[action]]);
    }, {section, actions});
    await expect.poll(published).toEqual(actions.flatMap(() => [2, false]));
    await page.evaluate(async () => {
      Object.values(window.secondaryReads).forEach(holds => holds[0]());
      await new Promise(resolve => requestAnimationFrame(resolve));
    });
    expect(await published()).toEqual(actions.flatMap(() => [2, false]));
    await expectHarnessClean(page, errors);
  });
}

for (const [pageName, section, ready] of [
  ["guide", null, "#guide-search"],
  ["usage-maintenance", "usage", "#usage-window"],
  ["usage-maintenance", "request-debug", "extended-openai-debug-panel"],
  ["usage-maintenance", "diagnostics", "#test-agent"],
  ["usage-maintenance", "retention", "#config-retention"],
  ["usage-maintenance", "backup-restore", "#transfer-export-mode"],
]) {
  test(`dirty configuration survives ${section || pageName} and returns without a configuration read`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics"));
    const panel = page.locator("extended-openai-management-panel");
    const draft = panel.locator('[data-config="__title"]');
    await expect(draft).toHaveValue("Jarvis");
    const reads = await page.evaluate(() => browserHarness.calls.filter(
      (call) => call.section === "configuration" && call.action === "get",
    ).length);
    await draft.fill("Preserved draft");
    if (section) {
      await panel.locator(`.top-nav button[data-page="${pageName}"]`).click();
      await panel.locator(`#local-section`).selectOption(section, {force: true});
    } else await panel.locator(`.top-nav button[data-page="${pageName}"]`).click();
    await expect(panel.locator(ready)).toBeVisible();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
    await expect(panel.locator('.top-nav button[data-page="assistant"]')).toHaveClass(/eoc-has-unsaved/);
    await panel.locator('.top-nav button[data-page="assistant"]').click();
    await expect(draft).toHaveValue("Preserved draft");
    await expect(panel.locator("#dirty-state")).toBeVisible();
    expect(await page.evaluate(() => browserHarness.calls.filter(
      (call) => call.section === "configuration" && call.action === "get",
    ).length)).toBe(reads);
    await expectHarnessClean(page, errors);
  });
}

test("assistant ownership and browser unload still protect a dirty draft", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Owned draft");
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})))).toBe(true);
  await panel.evaluate((host) => {
    host._data.agents.push({...host._data.agents[0], subentry_id: "agent-2", title: "Second assistant"});
    host._render();
  });
  await panel.locator("#agent").selectOption("agent-2");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-cancel").click();
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Owned draft");
  await panel.locator("#agent").selectOption("agent-2");
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator("#agent")).toHaveValue("agent-2");
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})))).toBe(false);
  await expectHarnessClean(page, errors);
});

test("dirty retention projection joins the full agent draft without losing its change", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/retention"));
  const panel = page.locator("extended-openai-management-panel");
  const retention = panel.locator('[data-config="usage_request_retention_days"]');
  await expect(retention).toBeVisible();
  const original = await retention.inputValue();
  const alternative = original === "7" ? "14" : "7";
  await retention.selectOption(alternative);
  await panel.locator('.top-nav button[data-page="assistant"]').click();
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expect(panel.locator("#dirty-state")).toBeVisible();
  await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();
  await panel.locator("#local-section").selectOption("retention", {force: true});
  await expect(retention).toHaveValue(alternative);
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
  await expectHarnessClean(page, errors);
});


for (const legacyDraft of [false, true]) {
  test(`archive retention survives navigation with matching saved value (legacy draft: ${legacyDraft})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    // Seed only the fixture transport's persisted configuration, then mount the
    // actual route. Navigation, editing and save all use production handlers.
    await page.evaluate(() => {
      const state = browserHarness.getState();
      Object.assign(state.configuration.config, {archive_enabled:true, archive_retention_days:30});
      state.configuration.options.archive_retention_days = [7, 30, 365].map(value => ({value, label:`${value} days`}));
      localStorage.setItem("extended-openai-browser-harness-state-v3", JSON.stringify(state));
    });
    await page.goto(fixtureUrl("data-memory/conversations"));
    const retention = panel.locator('[data-config="archive_retention_days"]');
    await expect(retention).toHaveValue("30");
    await retention.selectOption("365");
    expect(await panel.evaluate(host => host._draft.archive_retention_days)).toBe(365);
    await panel.locator('.top-nav button[data-page="assistant"]').click();
    await expect(panel.locator('[data-config="__title"]')).toBeVisible();
    if (legacyDraft) await panel.evaluate(host => {host._draft.archive_retention_days = "365";});
    await panel.evaluate(host => host._navigate("data-memory", "conversations"));
    await expect(retention).toHaveValue("365");
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(call => call.section === "configuration" && call.action === "save").length)).toBe(1);
    expect(await page.evaluate(() => browserHarness.calls.find(call => call.section === "configuration" && call.action === "save").config.archive_retention_days)).toBe(legacyDraft ? "365" : 365);
    await expect(retention).toHaveValue("365");
    await expectHarnessClean(page, errors);
  });
}
