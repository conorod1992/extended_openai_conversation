// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";

import {applyConfigurationControl} from "../../custom_components/extended_openai_conversation_responses/frontend/configuration-controls.js";
import {settingsResultsMarkup, SEARCH_DEBOUNCE_MS} from "../../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";
import {renderConfiguration} from "../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";
import {renderTools} from "../../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools.js";
import {renderBackupTransferPanel} from "../../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
import {renderExposedAttributeSettings} from "../../custom_components/extended_openai_conversation_responses/frontend/exposed-attributes-ui.js";

const owner = () => ({_e:(value) => String(value ?? ""), _titleCase:String, _empty:String, _draft:{}, _result:{options:{}}});

describe("native management rendering", () => {
  it("updates one draft field without scanning the whole form", () => {
    const panel = {_draft:{temperature:0.2}, _draftTitle:"Agent", shadowRoot:{querySelectorAll:() => { throw new Error("ordinary edits must not scan controls"); }}};
    const control = {dataset:{config:"temperature", type:"number"}, value:"0.7"};
    expect(applyConfigurationControl(panel, control)).toEqual({key:"temperature", changed:true});
    expect(panel._draft.temperature).toBe(0.7);
  });

  it("keeps title and structured voice mappings in the live draft", () => {
    const panel = {_draft:{}, _draftTitle:"Old"};
    expect(applyConfigurationControl(panel, {dataset:{config:"__title"}, value:"New"})).toEqual({key:"__title", changed:true});
    expect(panel._draftTitle).toBe("New");
    expect(applyConfigurationControl(panel, {dataset:{}, id:"voice-mappings", value:'{"satellite":"user"}'})).toEqual({key:"voice_device_mappings", changed:true});
    expect(panel._draft.voice_device_mappings).toEqual({satellite:"user"});
  });

  it("renders settings results independently of the full panel", () => {
    const panel = {
      _settingsSearchQuery:"timeout",
      _canAccessView:() => true,
      _e:(value) => String(value ?? ""),
    };
    const markup = settingsResultsMarkup(panel);
    expect(markup).toContain("Conversation timeout");
    expect(markup).toContain('data-target="config-conversation_timeout_minutes"');
    expect(markup).toContain("settings-result");
  });

  it("keeps the shipped renderer targeted to dynamic regions", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-renderer.js", import.meta.url), "utf8");
    expect(source).toContain("data-eoc-persistent-shell");
    expect(source).toContain("main.innerHTML =");
    expect(source).not.toContain("shadowRoot.innerHTML =");
    expect(source).not.toContain("event.stopImmediatePropagation()");
    expect(SEARCH_DEBOUNCE_MS).toBe(80);
  });

  it("keeps loaded main content mounted only during navigation busy renders", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
    expect(source).toContain('main.setAttribute("aria-busy", "true")');
    expect(source).toContain("main.inert = true;");
    expect(source).toContain("main.inert = false;");
    expect(source).toContain("panel._eocNavigationDepth = (panel._eocNavigationDepth || 0) + 1;");
    expect(source).toContain("trackAsync(this, NAVIGATION_MARK_PREFIX");
    expect(source).toContain("return undefined;");
    expect(source).not.toContain("document.createDocumentFragment()");
    expect(source).not.toContain("replaceChildren(fragment)");
    expect(source).toContain("extended-openai:navigation");
    expect(source).toContain("extended-openai:load-section");
    expect(source).toContain("extended-openai:render");
    expect(source).toContain("MAX_MEASURE_ENTRIES = 100");
    expect(source).not.toContain("initializeManagementPanel");
  });

  it("builds only configuration section bodies that pass the active filter", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js", import.meta.url), "utf8");
    const filterGuard = 'if (panel._configSectionFilter && !panel._configSectionFilter.has(id)) return "";';
    const bodyEvaluation = 'const content = typeof body === "function" ? body() : body;';
    expect(source).toContain(filterGuard);
    expect(source).toContain(bodyEvaluation);
    expect(source.indexOf(filterGuard)).toBeLessThan(source.indexOf(bodyEvaluation));
    for (const section of ["general", "conversation", "prompt", "capabilities", "archive", "voice", "speech", "context", "model", "retention", "backup"]) {
      expect(source).toContain(`section(panel,"${section}"`);
      const start = source.indexOf(`section(panel,"${section}"`);
      expect(source.slice(start, source.indexOf("\n", start))).toContain(",() => ");
    }
    expect(source).toContain('section(panel,"local","Local handling"');
    expect(source).toContain('() => renderLocalHandling(panel,config)');
    const panel = owner();
    panel._configSections = ["general"];
    Object.defineProperty(panel._draft, "prompt", {get() {throw new Error("Unselected prompt body evaluated");}});
    expect(renderConfiguration(panel)).toContain('id="config-general"');
  });

  it("renders model decisions directly and only loads catalog data for model controls", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js", import.meta.url), "utf8");
    expect(source).toContain("const hasModelAwareControls = Boolean(");
    expect(source).toContain("if (hasModelAwareControls) void ensureCatalogData(panel);");
    const panel = owner();
    panel._configSections = ["model"];
    panel._draft.reasoning_effort = "high";
    panel._result.model_capabilities = {supports_reasoning_effort:true, reasoning:{supported:true,efforts:["low","high"]}};
    expect(renderConfiguration(panel)).toMatch(/value="high"[^>]*selected>High/);
    panel._result.model_capabilities.reasoning.supported = false;
    expect(renderConfiguration(panel)).not.toContain('data-config="reasoning_effort"');
  });

  it("renders local, prompt and backup content without DOM parsing", () => {
    const previous = globalThis.document;
    globalThis.document = {createElement() {throw new Error("Unexpected render parsing");}};
    try {
      const panel = owner();
      panel._configSections = ["local","prompt","backup"];
      const html = renderConfiguration(panel, {
        renderBackup: renderBackupTransferPanel,
        renderExposedAttributes: renderExposedAttributeSettings,
      });
      expect(html).toContain("local-handling-explainer");
      expect(html).toContain("exposed-attribute");
      expect(html).toContain("transfer-panel");
      expect(html).not.toContain("config-jumps");
    } finally { globalThis.document = previous; }
  });

  it("caches only clean stable configuration subsections", () => {
    const panel = owner();
    panel._configSections = ["speech"];
    let escapes = 0;
    panel._e = (value) => {escapes += 1; return String(value ?? "");};
    renderConfiguration(panel);
    escapes = 0;
    renderConfiguration(panel);
    expect(escapes).toBe(0);
    panel._configDirty = true;
    renderConfiguration(panel);
    expect(escapes).toBeGreaterThan(0);
    panel._configDirty = false;
    panel._result = {...panel._result};
    escapes = 0;
    renderConfiguration(panel);
    expect(escapes).toBeGreaterThan(0);
    panel._configSections = ["model"];
    renderConfiguration(panel);
    escapes = 0;
    renderConfiguration(panel);
    expect(escapes).toBeGreaterThan(0);
  });

  it("renders Function Group state and member assignment together", () => {
    const panel = owner();
    panel._draft = {functions:[{spec:{name:"one"}}],function_groups:[{id:"g",name:"Group",enabled:false,functions:["one"]}]};
    const html = renderTools(panel);
    expect(html).toContain("group-disabled-badge");
    expect(html).toMatch(/class="secondary edit-group"[^>]*disabled/);
    expect(html).toContain('class="function-group-assignment"');
    expect(html).toContain('Group (disabled)</option>');
    expect(html.match(/class="group-enabled"/g)).toHaveLength(1);
  });

  it("starts lazy view data loads alongside their frontend assets", async () => {
    const loadingSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-route.js", import.meta.url), "utf8");
    const routeSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-route.js", import.meta.url), "utf8");
    expect(loadingSource).toContain("sectionPromise = Promise.resolve(");
    expect(loadingSource).toContain("Promise.allSettled([assetPromise, sectionPromise])");
    expect(loadingSource).toContain('view === "overview"');
    expect(loadingSource).not.toContain("prototype.");
    expect(loadingSource).toContain("loadSectionData.call(panel, silent)");
    expect(loadingSource).toContain("_eocViewAssetToken");
    expect(routeSource).toContain("let loadData = () => loadRouteData(panel, silent, view, token);");
    expect(routeSource).toContain("if (feature && DATA_FEATURES.has(view))");
    expect(routeSource).toContain("return loadSectionAlongsideAsset(panel, silent, loadData, view, Promise.all([feature, asset]), token);");
    expect(routeSource).toContain("Promise.allSettled([assetPromise, sectionPromise])");
  });
});
