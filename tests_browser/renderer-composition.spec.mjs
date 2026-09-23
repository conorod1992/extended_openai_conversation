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
    const {renderRequestRules} = await import(`${path}request-rules-ui.js`);
    const {requestRulesDialog} = await import(`${path}request-rules-ui-impl.js`);
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
    const editor = await import(`${path}agent-config-editor-base.js`);
    const toolsEditor = await import(`${path}agent-config-tools.js`);
    const backup = await import(`${path}backup-transfer-ui.js`);
    const exposedAttributes = await import(`${path}exposed-attributes-ui.js`);
    const presentation = {renderBackup:backup.renderBackupTransferPanel, renderExposedAttributes:exposedAttributes.renderExposedAttributeSettings};
    const panel = window.browserHarness.panel;
    panel._configSections = ["local", "prompt", "backup"];
    panel._configDirty = true;
    panel._draft.local_intents_enabled = false;
    panel._draft.local_intent_delayed_commands_to_ai = true;
    panel._draft.exposed_entity_attributes = {"registry:lamp":["brightness"]};
    panel._exposedAttributeEntityId = "light.lamp";
    panel._result.exposed_attribute_catalog = {entities:[{entity_id:"light.lamp",reference:"registry:lamp",name:"<Lamp>",attributes:["brightness","color_mode"],durable_selection_available:true}]};
    panel._result.local_handling = {intents:[{intent:"HassTurnOn", label:"Turn on"}]};
    panel._draft.function_groups = [{id:"group",name:"Group <one>",description:"Keep & show",enabled:false,loading_mode:"on_demand",functions:[]}];
    const root = document.createElement("template");
    root.innerHTML = editor.renderConfiguration(panel, presentation);
    const delayed = root.content.querySelector('[data-config="local_intent_delayed_commands_to_ai"]');
    const exposed = root.content.querySelector(".context-toggles");
    const configuration = {delayed:[delayed.checked, delayed.disabled, delayed.closest("#local-intent-list") !== null], jumps:root.content.querySelectorAll(".config-jumps").length, local:root.content.querySelectorAll(".local-handling-explainer").length, exposed:exposed.innerHTML, exposedPosition:exposed.querySelector('[data-field="exposed_entities_enabled"]').nextElementSibling.className, attributes:[...exposed.querySelectorAll("[data-exposed-attribute]")].map((input) => [input.dataset.attribute,input.checked]), transfer:root.content.querySelector(".transfer-panel").textContent, dialog:backup.renderRestoreTransferDialog(panel)};
    root.innerHTML = toolsEditor.renderTools(panel);
    const group = root.content.querySelector('[data-group-id="group"]');
    return {...configuration, group:{disabled:group.classList.contains("is-disabled"), badge:group.querySelector(".group-disabled-badge").textContent, edit:group.querySelector(".edit-group").disabled, checked:group.querySelector(".group-enabled").checked, name:group.querySelector("h3").textContent}};
  }, frontend);
  expect(result.delayed).toEqual([true, true, true]);
  expect(result.jumps).toBe(0);
  expect(result.local).toBe(1);
  expect(result.exposed).toContain("exposed-attribute");
  expect(result.exposed).toContain("&lt;Lamp&gt;");
  expect(result.exposedPosition).toBe("exposed-attribute-settings");
  expect(result.attributes).toEqual([["brightness",true],["color_mode",false]]);
  expect(result.transfer).toContain("Secret-looking values");
  expect(result.dialog).toContain("Your unsaved configuration changes will be discarded");
  expect(result.dialog).toContain("Sections to replace");
  expect(result.group).toEqual({disabled:true, badge:"Disabled", edit:true, checked:false, name:"Group <one>"});
});

test("render owners emit final markup without creating a template in a browser", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  await expect(page.locator('extended-openai-management-panel [data-config="chat_model"]')).toBeVisible();
  const result = await page.evaluate(async (path) => {
    const editor = await import(`${path}agent-config-editor-base.js`);
    const toolsEditor = await import(`${path}agent-config-tools.js`);
    const backup = await import(`${path}backup-transfer-ui.js`);
    const exposedAttributes = await import(`${path}exposed-attributes-ui.js`);
    const presentation = {renderBackup:backup.renderBackupTransferPanel, renderExposedAttributes:exposedAttributes.renderExposedAttributeSettings};
    const guide = await import(`${path}guide-page.js`);
    const rules = await import(`${path}request-rules-ui.js`);
    await guide.ensureGuideModule();
    const {panel} = window.browserHarness;
    panel._configSections = ["general", "model", "local", "prompt", "backup"];
    const create = document.createElement;
    let html;
    try {
      document.createElement = function(tag, ...args) {
        if (tag === "template") throw new Error("Production renderer reparsed its output");
        return create.call(this, tag, ...args);
      };
      html = editor.renderConfiguration(panel, presentation) + toolsEditor.renderTools(panel)
        + toolsEditor.configurationDialogs({_e:panel._e.bind(panel), _viewKey:() => "capabilities/functions"}) + backup.renderRestoreTransferDialog(panel)
        + rules.renderRequestRules(panel) + rules.requestRulesDialog()
        + guide.renderGuide(panel);
    } finally { document.createElement = create; }
    const root = document.createElement("template");
    root.innerHTML = html;
    return {groups:root.content.querySelectorAll(".group-enabled").length, native:root.content.querySelectorAll("#tool-yaml-native").length, backup:root.content.querySelectorAll(".transfer-panel").length, scopes:root.content.querySelectorAll("#restore-transfer-sections").length};
  }, frontend);
  expect(result).toEqual({groups:1, native:1, backup:1, scopes:1});
});

test("Function Group switches save once and preserve individually disabled members", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('.group-enabled[data-group-id="baseline-group"]')).toBeVisible();
  await page.evaluate(() => { window.browserHarness.panel._render(); window.browserHarness.panel._render(); });
  const group = panel.locator('.function-group-card[data-group-id="baseline-group"]');
  await group.locator(".group-enabled").uncheck();
  await expect(group.locator(".edit-group")).toBeDisabled();
  await expect(group.locator(".group-disabled-badge")).toHaveCount(1);
  await expect(group.locator(".group-enabled")).toHaveAttribute("role", "switch");
  await group.locator("summary").click();
  const tool = group.locator('.tool-card').filter({hasText:"baseline_tool"});
  await tool.locator(".tool-enabled").uncheck();
  await group.locator(".group-enabled").check();
  await expect(group.locator(".edit-group")).toBeEnabled();
  await expect(group.locator(".group-disabled-badge")).toHaveCount(0);
  await group.locator("summary").click();
  await expect(tool.locator(".tool-enabled")).not.toBeChecked();
  const calls = await page.evaluate(() => window.browserHarness.calls.filter((call) => call.section === "tools" && call.action === "save_group"));
  expect(calls).toHaveLength(2);
  expect(calls.map((call) => call.group.enabled)).toEqual([false,true]);
});

test("live configuration routes select their final sections without reparsing", async ({page}) => {
  for (const route of ["capabilities/home-assistant", "capabilities/web-skills", "assistant/conversation", "assistant/model-responses", "assistant/voice"]) {
    await page.goto(fixtureUrl(route));
    await expect(page.locator("extended-openai-management-panel .config-section").first()).toBeVisible();
    const result = await page.evaluate(() => {
      const {panel} = window.browserHarness;
      const create = document.createElement;
      let html;
      try {
        document.createElement = function(tag, ...args) {
          if (tag === "template") throw new Error("Route reparsed configuration HTML");
          return create.call(this, tag, ...args);
        };
        html = panel._content(panel._selectedAgent());
      } finally { document.createElement = create; }
      const host = document.createElement("template");
      host.innerHTML = html;
      return {html, heading:host.content.querySelector(".config-section-heading .eyebrow")?.textContent, local:host.content.querySelectorAll("#config-local").length, memory:host.content.querySelectorAll('[data-config="memory_auto_retrieve_limit"],[data-config="shared_memory_mode"],[data-config="memory_mode"],[data-config="temporary_memory"]').length, voice:host.content.querySelectorAll(".voice-identity-flow").length};
    });
    expect(result.memory).toBe(0);
    expect(result.local).toBe(route === "capabilities/home-assistant" ? 1 : 0);
    expect(result.voice).toBe(route === "assistant/voice" ? 1 : 0);
    if (route === "capabilities/web-skills") {
      expect(result.heading).toBe("Web search & Skills");
      expect(result.html).not.toContain('data-config="knowledge_enabled"');
    }
  }
});
