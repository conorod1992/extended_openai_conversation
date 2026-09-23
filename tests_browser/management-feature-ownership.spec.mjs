import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("memory kinds, scope ownership and load-more survive rerenders without duplicate actions", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-memory")).toBeVisible();
  await page.evaluate(async () => {
    const {panel, hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.featureCalls = [];
    hass.callWS = async (message) => {
      if (message.section === "scopes" && message.scope_kind === "temporary") {
        return {scopes:[
          {scope_id:"user:test-user", scope_type:"user", display_name:"Test User", is_current_user:true, temporary_memory_count:1},
          {scope_id:"shared:household", scope_type:"shared", display_name:"Shared household", temporary_memory_count:1},
        ]};
      }
      if (message.section === "memories") {
        window.featureCalls.push(message);
        if (message.action === "list") return {memories:[{memory_id:`m-${message.offset || 0}`, content:`Memory ${message.offset || 0}`, category:"general", source:"manual"}], has_more: !message.offset};
        if (message.action === "temporary_list") return {memories:[{memory_id:"t-1",content:"Temporary visitor",category:"visitors",owner_scope_id:message.scope_id,expires_at:"2027-01-01T12:00:00Z"}]};
      }
      return original(message);
    };
    panel._data.scopes.push({scope_id:"shared:household", scope_type:"shared", display_name:"Shared household"}, {scope_id:"__anonymous__",scope_type:"anonymous_legacy",display_name:"Legacy"});
    await panel._loadSection(true);
    panel._render();
    panel._render();
  });
  await panel.locator("#load-more-memories").click();
  await expect(panel.locator(".memory-list .list-card")).toHaveCount(2);
  expect(await page.evaluate(() => window.featureCalls.filter((c) => c.action === "list" && c.offset === 1).length)).toBe(1);
  await panel.locator('.memory-kind[data-kind="temporary"]').click();
  await expect(panel.getByText("Temporary visitor", {exact:true})).toBeVisible();
  await expect(panel.locator('#scope option[value="__anonymous__"]')).toHaveCount(0);
  await panel.locator("#scope").selectOption("shared:household");
  await expect.poll(() => page.evaluate(() => window.featureCalls.filter((c) => c.action === "temporary_list").at(-1)?.scope_id)).toBe("shared:household");
  await panel.locator("button.edit-temporary-memory").click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#temporary-memory-content")).toHaveValue("Temporary visitor");
  await panel.locator("#temporary-memory-dialog").getByRole("button", {name:"Cancel",exact:true}).click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", false);
  await panel.locator('.memory-kind[data-kind="persistent"]').click();
  await expect(panel.locator("#add-memory")).toBeVisible();
  await expect(panel.locator("#scope")).toHaveValue("shared:household");
  await expectHarnessClean(page, errors);
});

test("Knowledge availability loads, edits and submits once after normal rerenders", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-source")).toBeVisible();
  await page.evaluate(async () => {
    const {panel, hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    const source = {source_id:"source-1",title:"House notes",description:"Reference",content:"Keep this source",enabled:false};
    window.featureCalls = [];
    hass.callWS = async (message) => {
      if (message.section === "knowledge") {
        window.featureCalls.push(message);
        if (message.action === "list") return {sources:[source]};
        if (message.action === "get") return {source};
        if (message.action === "update") { Object.assign(source, message); return {source}; }
      }
      return original(message);
    };
    panel._sectionCache.clear();
    await panel._loadSection(true);
    panel._render();
    panel._render();
  });
  await expect(panel.locator(".knowledge-source-availability-badge")).toHaveText("Unavailable");
  await panel.locator(".source-edit-button").click();
  await expect(panel.locator("#knowledge-content")).toHaveValue("Keep this source");
  await expect(panel.locator("#knowledge-source-enabled")).not.toBeChecked();
  await panel.locator("#knowledge-source-enabled").check();
  await panel.locator("#knowledge-save").click();
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator(".knowledge-source-availability-badge")).toHaveText("Available");
  const updates = await page.evaluate(() => window.featureCalls.filter((c) => c.action === "update"));
  expect(updates).toHaveLength(1);
  expect(updates[0]).toMatchObject({source_id:"source-1",enabled:true,content:"Keep this source"});
  await panel.locator("#add-source").click();
  await expect(panel.locator("#knowledge-source-enabled")).toBeChecked();
  await expectHarnessClean(page, errors);
});

test("lazy history helpers retain search pages and guard session dialogs", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/conversations"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#archive-query")).toBeVisible();
  await page.evaluate(() => {
    const {hass} = window.browserHarness;
    const original = hass.callWS.bind(hass);
    window.featureCalls = [];
    hass.callWS = async (message) => {
      if (message.section === "conversations") {
        window.featureCalls.push(message);
        if (message.action === "search") return {results:[{session_id:"s-1",title:`Match ${message.offset}`,timestamp:"2026-09-01"}],offset:message.offset,limit:20,returned:1,total:2,has_more:message.offset === 0,next_offset:20};
        if (message.action === "get") return {session:{title:"Retained session"},turns:[{user_text:`Question ${message.start_turn}`,assistant_text:"Answer"}],offset:message.start_turn,limit:20,returned:1,total:2,has_more:message.start_turn === 0,next_offset:20};
      }
      return original(message);
    };
  });
  await panel.locator("#archive-query").fill("project");
  await panel.evaluate(host => { host._bindActions(); host._bindActions(); });
  await panel.locator("#archive-search").click();
  await expect(panel.getByRole("heading", {name:"Match 0",exact:true})).toBeVisible();
  await page.evaluate(() => { window.browserHarness.panel._render(); window.browserHarness.panel._render(); });
  await expect(panel.locator("#archive-query")).toHaveValue("project");
  await expect(panel.locator(".eoc-history-pager")).toHaveCount(1);
  await panel.locator(".eoc-history-pager").getByRole("button", {name:"Next",exact:true}).click();
  await expect(panel.getByRole("heading", {name:"Match 20",exact:true})).toBeVisible();
  expect(await page.evaluate(() => window.featureCalls.filter((c) => c.action === "search" && c.offset === 20).length)).toBe(1);
  await panel.evaluate(host => { host._bindActions(); host._bindActions(); });
  await panel.locator(".open-session").focus();
  await panel.locator(".open-session").press("Enter");
  await expect(panel.locator("#session-body")).toContainText("Question 0");
  await panel.getByRole("button", {name:"Next turns",exact:true}).click();
  await expect(panel.locator("#session-body")).toContainText("Question 20");
  await expect(panel.locator(".eoc-turn-pager")).toHaveCount(1);
  expect(await page.evaluate(() => window.featureCalls.filter((c) => c.action === "get").map((c) => [c.start_turn,c.limit]))).toEqual([[0,20],[20,20]]);
  await page.keyboard.press("Escape");
  await expect(panel.locator("#session-dialog")).toHaveJSProperty("open", false);
  await panel.locator(".view-session").click();
  await expect(panel.locator("#session-body")).toContainText("Question 0");
  expect(await page.evaluate(() => window.featureCalls.filter(c => c.action === "get").length)).toBe(3);
  await expectHarnessClean(page, errors);
});
