// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";

import {applyIncrementalDraftUpdate, settingsResultsMarkup} from "../../custom_components/extended_openai_conversation_responses/frontend/management-rendering-performance.js";

describe("legacy management rendering optimizations", () => {
  it("updates one draft field without scanning the whole form", () => {
    const panel = {_draft:{temperature:0.2}, _draftTitle:"Agent"};
    const control = {dataset:{config:"temperature", type:"number"}, value:"0.7"};
    expect(applyIncrementalDraftUpdate(panel, control, {querySelectorAll:() => []})).toBe(true);
    expect(panel._draft.temperature).toBe(0.7);
  });

  it("keeps title and structured voice mappings in the live draft", () => {
    const panel = {_draft:{}, _draftTitle:"Old"};
    expect(applyIncrementalDraftUpdate(panel, {dataset:{config:"__title"}, value:"New"}, {})).toBe(true);
    expect(panel._draftTitle).toBe("New");
    expect(applyIncrementalDraftUpdate(panel, {dataset:{}, id:"voice-mappings", value:'{"satellite":"user"}'}, {})).toBe(true);
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

  it("keeps the shipped renderer patch targeted to dynamic regions", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-rendering-performance.js", import.meta.url), "utf8");
    expect(source).toContain("data-eoc-persistent-shell");
    expect(source).toContain("main.innerHTML =");
    expect(source).not.toContain("shadowRoot.innerHTML =");
    expect(source).toContain("event.stopImmediatePropagation()");
    expect(source).toContain("SEARCH_DEBOUNCE_MS = 80");
  });

  it("keeps loaded main content mounted only during navigation busy renders", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url), "utf8");
    expect(source).toContain('main.setAttribute("aria-busy", "true")');
    expect(source).toContain("main.inert = true;");
    expect(source).toContain("main.inert = false;");
    expect(source).toContain("panel._eocNavigationDepth > 0");
    expect(source).toContain("if (navigation) this._eocNavigationDepth = (this._eocNavigationDepth || 0) + 1;");
    expect(source).toContain("wrapAsyncMethod(prototype, \"_navigate\", NAVIGATION_MARK_PREFIX, true);");
    expect(source).toContain("return undefined;");
    expect(source).not.toContain("document.createDocumentFragment()");
    expect(source).not.toContain("replaceChildren(fragment)");
    expect(source).toContain("extended-openai:navigation");
    expect(source).toContain("extended-openai:load-section");
    expect(source).toContain("extended-openai:render");
    expect(source).toContain("MAX_MEASURE_ENTRIES = 100");
    expect(source).not.toContain("management-hot-path-performance.js");
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
      expect(source.slice(start, start + 320)).toContain(",() => `");
    }
    expect(source).toContain('section(panel,"local","Local handling"');
    expect(source).toContain('() => renderLocalHandling(panel,config)');
  });

  it("runs model DOM decoration only where model-aware controls exist", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js", import.meta.url), "utf8");
    const guard = "if (!source.includes('id=\"config-general\"') && !source.includes('id=\"config-model\"')) return html;";
    expect(source).toContain(guard);
    expect(source.indexOf(guard)).toBeLessThan(source.indexOf('document.createElement("template")'));
    expect(source).toContain("const hasModelAwareControls = Boolean(");
    expect(source).toContain("if (hasModelAwareControls) void ensureCatalogData(panel);");
  });

  it("skips unrelated configuration decorator parse passes", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
    expect(source).toContain("if (!stripped.includes('id=\"config-local\"')) return stripped;");
    expect(source).toContain('.replace("Maximum tool calls per conversation", "Maximum tool calls per request")');
    expect(source).toContain("if (!String(html || \"\").includes('id=\"config-prompt\"')) return html;");
    expect(source).toContain("html.includes('id=\"config-backup\"') ? decorateBackupMarkup(html) : html");
  });

  it("caches only clean stable configuration subsections", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
    expect(source).toContain('const CACHEABLE_CONFIG_SECTIONS = new Set(["capabilities", "archive", "voice", "speech", "context", "retention", "backup"]);');
    expect(source).toContain("if (panel?._configDirty) return null;");
    expect(source).toContain("state.result !== panel._result");
    expect(source).toContain("MAX_CONFIG_RENDER_CACHE_ENTRIES = 8");
    expect(source).toContain("const cached = getCachedConfigurationMarkup(panel, cacheKey);");
    expect(source).toContain("if (cached !== null) return cached;");
  });

  it("decorates Function Groups in the existing assignment DOM pass", async () => {
    const nativeSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js", import.meta.url), "utf8");
    const editorSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
    expect(nativeSource).toContain("decorateFunctionGroupCards(panel, template.content, groups);");
    expect(nativeSource).toContain("style.dataset.functionGroupsDecorated = \"\";");
    expect(editorSource).toContain('includes("data-function-groups-decorated")');
    expect(editorSource.indexOf('includes("data-function-groups-decorated")')).toBeLessThan(editorSource.indexOf('const template = document.createElement("template")', editorSource.indexOf("function decorateFunctionGroups")));
  });

  it("starts lazy view data loads alongside their frontend assets", async () => {
    const loadingSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-loading-performance.js", import.meta.url), "utf8");
    const routeSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-route-performance.js", import.meta.url), "utf8");
    expect(loadingSource).toContain("sectionPromise = Promise.resolve(");
    expect(loadingSource).toContain("Promise.allSettled([assetPromise, sectionPromise])");
    expect(loadingSource).toContain('view === "overview"');
    expect(loadingSource).toContain("loadOverview(panel, silent)");
    expect(loadingSource).toContain("originalLoadSection.call(panel, silent)");
    expect(loadingSource).toContain("_eocViewAssetToken");
    expect(routeSource).toContain("sectionPromise = Promise.resolve(originalLoadSection.call(panel, silent));");
    expect(routeSource).toContain("Promise.allSettled([assetPromise, sectionPromise])");
  });
});
