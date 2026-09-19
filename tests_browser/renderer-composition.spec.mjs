import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

const frontend = "/custom_components/extended_openai_conversation_responses/frontend/";

test("Guide composes final groups, selected topics and destinations immediately", async ({page}) => {
  await page.goto(fixtureUrl("guide"));
  await expect(page.locator("extended-openai-management-panel #guide-search")).toBeVisible();
  const result = await page.evaluate(async (path) => {
    const {renderGuide} = await import(`${path}guide-page-impl.js`);
    const panel = window.browserHarness.panel;
    panel._guideTopic = "backup-restore";
    const root = document.createElement("template");
    root.innerHTML = renderGuide(panel);
    const groups = [...root.content.querySelectorAll(".guide-group")].map((group) => [group.dataset.guideGroup, [...group.querySelectorAll(".guide-topic")].map((topic) => topic.id)]);
    const backup = root.content.querySelector("#guide-backup-restore");
    const local = root.content.querySelector("#guide-local-handling .guide-action");
    panel._guideQuery = "request debugging";
    const searched = renderGuide(panel);
    panel._guideQuery = "";
    return {groups, open:backup.open, destination:[local.dataset.page, local.dataset.subsection], searched, text:root.content.textContent};
  }, frontend);
  expect(result.groups.find(([id]) => id === "operations")[1]).toEqual(["guide-backup-restore", "guide-request-debugging", "guide-model-data", "guide-usage"]);
  expect(result.groups.find(([id]) => id === "voice-speech")[1]).toEqual(["guide-quiet-hours", "guide-voice", "guide-speech-processing"]);
  expect(result.open).toBe(true);
  expect(result.destination).toEqual(["capabilities", "home-assistant"]);
  expect(result.searched).toContain('id="guide-request-debugging"');
  expect(result.text).toContain("Treat captured requests as private");
  expect(result.text).not.toContain("Request debugging and backups");
});

test("Request Rules render diagnostics, search and global reorder boundaries", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await expect(page.locator("extended-openai-management-panel #rule-search")).toBeVisible();
  const result = await page.evaluate(async (path) => {
    const {renderRequestRules, requestRulesDialog} = await import(`${path}request-rules-ui.js`);
    const panel = window.browserHarness.panel;
    panel._result = {rules:["First", "Middle", "Last"].map((name, order) => ({id:String(order), name, order, enabled:true, phrases:[name], match_type:"equals", action_type:"local_action", action:{actions:[]}})), diagnostics:{1:"Old <pattern>"}};
    panel._query = "Middle";
    panel._eocInPlaceRequestRuleSearch = false;
    const root = document.createElement("template");
    root.innerHTML = renderRequestRules(panel);
    const filtered = [...root.content.querySelectorAll(".rule-move")].map((node) => [node.dataset.direction, node.disabled]);
    panel._eocInPlaceRequestRuleSearch = true;
    root.innerHTML = renderRequestRules(panel);
    return {filtered, query:root.content.querySelector("#rule-search").value, state:panel._query, count:root.content.querySelectorAll(".request-rule-card").length, moves:[...root.content.querySelectorAll(".rule-move")].map((node) => node.disabled), diagnostic:root.content.querySelector(".sensitive-warning").textContent, dialog:requestRulesDialog()};
  }, frontend);
  expect(result.filtered).toEqual([["up", false], ["down", false]]);
  expect(result.moves).toEqual([true, false, false, false, false, true]);
  expect(result.count).toBe(3);
  expect(result.query).toBe("Middle");
  expect(result.state).toBe("Middle");
  expect(result.diagnostic).toContain("Old <pattern>");
  for (const id of ["rule-action-sequence-host", "rule-continue-to-ai", "sentence-pattern-builder", "rule-routing-scope-help"]) expect(result.dialog).toContain(`id="${id}"`);
});

test("configuration emits the shipped local, exposed-attribute, group and transfer presentation", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const result = await page.evaluate(async (path) => {
    const editor = await import(`${path}agent-config-editor.js`);
    const panel = window.browserHarness.panel;
    panel._configSections = ["local", "prompt", "backup"];
    panel._configDirty = true;
    panel._draft.local_intents_enabled = false;
    panel._draft.local_intent_delayed_commands_to_ai = true;
    panel._result.local_handling = {intents:[{intent:"HassTurnOn", label:"Turn on"}]};
    panel._draft.function_groups = [{id:"group",name:"Group <one>",description:"Keep & show",enabled:false,loading_mode:"on_demand",functions:[]}];
    const root = document.createElement("template");
    root.innerHTML = editor.renderConfiguration(panel);
    const delayed = root.content.querySelector('[data-config="local_intent_delayed_commands_to_ai"]');
    const exposed = root.content.querySelector(".context-toggles");
    const configuration = {delayed:[delayed.checked, delayed.disabled, delayed.closest("#local-intent-list") !== null], jumps:root.content.querySelectorAll(".config-jumps").length, local:root.content.querySelectorAll(".local-handling-explainer").length, exposed:exposed.innerHTML, transfer:root.content.querySelector(".transfer-panel").textContent, dialog:editor.restoreDialog(panel)};
    root.innerHTML = editor.renderTools(panel);
    const group = root.content.querySelector('[data-group-id="group"]');
    return {...configuration, group:{disabled:group.classList.contains("is-disabled"), badge:group.querySelector(".group-disabled-badge").textContent, edit:group.querySelector(".edit-group").disabled, checked:group.querySelector(".group-enabled").checked, name:group.querySelector("h3").textContent}};
  }, frontend);
  expect(result.delayed).toEqual([true, true, true]);
  expect(result.jumps).toBe(0);
  expect(result.local).toBe(1);
  expect(result.exposed).toContain("exposed-attribute");
  expect(result.transfer).toContain("Secret-looking values");
  expect(result.dialog).toContain("Your unsaved configuration changes will be discarded");
  expect(result.dialog).toContain("Sections to replace");
  expect(result.group).toEqual({disabled:true, badge:"Disabled", edit:true, checked:false, name:"Group <one>"});
});
