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

  it("keeps loaded main content mounted while a section refresh is busy", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url), "utf8");
    expect(source).toContain("document.createDocumentFragment()");
    expect(source).toContain("currentMain.replaceChildren(fragment)");
    expect(source).toContain('currentMain.setAttribute("aria-busy", "true")');
    expect(source).toContain("extended-openai:navigation");
    expect(source).toContain("extended-openai:load-section");
    expect(source).toContain("extended-openai:render");
    expect(source).toContain("MAX_MEASURE_ENTRIES = 100");
    expect(source).not.toContain("management-hot-path-performance.js");
  });

  it("runs model DOM decoration only where model-aware controls exist", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-model-v2.js", import.meta.url), "utf8");
    const guard = "if (!source.includes('id=\"config-general\"') && !source.includes('id=\"config-model\"')) return html;";
    expect(source).toContain(guard);
    expect(source.indexOf(guard)).toBeLessThan(source.indexOf('document.createElement("template")'));
  });

  it("skips unrelated configuration decorator parse passes", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
    expect(source).toContain("const needsDecoration = stripped.includes('id=\"config-local\"')");
    expect(source).toContain("if (!needsDecoration) return stripped;");
    expect(source).toContain("if (!String(html || \"\").includes('id=\"config-prompt\"')) return html;");
    expect(source).toContain("html.includes('id=\"config-backup\"') ? decorateBackupMarkup(html) : html");
  });

  it("decorates Function Groups in the existing assignment DOM pass", async () => {
    const nativeSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js", import.meta.url), "utf8");
    const editorSource = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
    expect(nativeSource).toContain("decorateFunctionGroupCards(panel, template.content, groups);");
    expect(nativeSource).toContain("style.dataset.functionGroupsDecorated = \"\";");
    expect(editorSource).toContain('includes("data-function-groups-decorated")');
    expect(editorSource.indexOf('includes("data-function-groups-decorated")')).toBeLessThan(editorSource.indexOf('const template = document.createElement("template")', editorSource.indexOf("function decorateFunctionGroups")));
  });
});
