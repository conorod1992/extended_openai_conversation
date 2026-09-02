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
    expect(markup).toContain("Conversation continuity");
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
});
