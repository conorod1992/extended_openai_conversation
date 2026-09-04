// @ts-nocheck
import {describe, expect, it} from "vitest";
import {readFile} from "node:fs/promises";

import {searchSettings} from "../../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";
import {ensureSearchConfiguration, searchMarkup} from "../../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";
import {
  SETTINGS_SEARCH_PROJECTION,
  buildSettingsSearchProjection,
  searchProjectedSettings,
} from "../../custom_components/extended_openai_conversation_responses/frontend/settings-search-index.js";

const escape = (value) => String(value ?? "");

function searchPanel(call) {
  return {
    _agentId: "agent-1",
    _data: {is_admin:true},
    _settingsSearchQuery: "conversation timeout",
    _settingsSearchConfig: null,
    _settingsSearchConfigAgentId: null,
    _settingsSearchConfigLoading: false,
    _settingsSearchConfigPromise: null,
    _settingsSearchConfigPromiseAgentId: null,
    _settingsSearchConfigError: null,
    _settingsSearchConfigErrorAgentId: null,
    _draft: null,
    _draftAgentId: null,
    _configData: null,
    _canAccessView: () => true,
    _e: escape,
    _call: call,
    _render: () => {},
  };
}

describe("settings search projection", () => {
  it("preserves the existing matching and ranking semantics", () => {
    for (const query of ["conversation timeout", "memory", "web search detail", "backup", "tools functions", "processing tier"]) {
      expect(searchProjectedSettings(query)).toEqual(searchSettings(query));
    }
    expect(SETTINGS_SEARCH_PROJECTION.length).toBeGreaterThan(0);
  });

  it("projects static searchable text once and reuses it across searches", () => {
    const reads = {label:0, description:0, terms:0, configKey:0};
    const item = {};
    for (const key of Object.keys(reads)) {
      Object.defineProperty(item, key, {
        get() {
          reads[key] += 1;
          return key === "label" ? "Conversation timeout" : key === "configKey" ? "conversation_timeout_minutes" : "timeout inactivity";
        },
      });
    }
    const projection = buildSettingsSearchProjection([item]);
    expect(reads).toEqual({label:1, description:1, terms:1, configKey:1});

    expect(searchProjectedSettings("timeout", projection)).toEqual([item]);
    expect(searchProjectedSettings("conversation timeout", projection)).toEqual([item]);
    expect(reads).toEqual({label:1, description:1, terms:1, configKey:1});
  });

  it("keeps management search on the projection rather than rebuilding per candidate", async () => {
    const source = await readFile(new URL("../../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js", import.meta.url), "utf8");
    expect(source).toContain('import {searchProjectedSettings} from "./settings-search-index.js"');
    expect(source).toContain("searchProjectedSettings(panel._settingsSearchQuery)");
    expect(source).not.toContain("searchSettings(panel._settingsSearchQuery)");
  });
});

describe("settings search configuration loading", () => {
  it("surfaces a failed initial load without publishing a false loaded state", async () => {
    let calls = 0;
    const panel = searchPanel(async () => {
      calls += 1;
      throw new Error("offline");
    });

    await ensureSearchConfiguration(panel);

    expect(calls).toBe(1);
    expect(panel._settingsSearchConfig).toBeNull();
    expect(panel._settingsSearchConfigAgentId).toBeNull();
    expect(panel._settingsSearchConfigLoading).toBe(false);
    expect(panel._settingsSearchConfigError).toBe(true);
    expect(panel._settingsSearchConfigErrorAgentId).toBe("agent-1");
    const markup = searchMarkup(panel);
    expect(markup).toContain("Current setting values couldn’t be loaded.");
    expect(markup).toContain('id="settings-search-retry"');
    expect(markup).toContain("Conversation timeout");
  });

  it("waits for explicit retry and recovers after the transient failure", async () => {
    let calls = 0;
    const panel = searchPanel(async () => {
      calls += 1;
      if (calls === 1) throw new Error("offline");
      return {config:{conversation_timeout_minutes:30}};
    });

    await ensureSearchConfiguration(panel);
    expect(calls).toBe(1);

    await ensureSearchConfiguration(panel);
    expect(calls).toBe(1);

    await ensureSearchConfiguration(panel, {retry:true});
    expect(calls).toBe(2);
    expect(panel._settingsSearchConfigAgentId).toBe("agent-1");
    expect(panel._settingsSearchConfig?.config?.conversation_timeout_minutes).toBe(30);
    expect(panel._settingsSearchConfigError).toBeNull();
    expect(panel._settingsSearchConfigErrorAgentId).toBeNull();
    expect(searchMarkup(panel)).toContain("Current: 30 min");
    expect(searchMarkup(panel)).not.toContain("settings-search-retry");
  });

  it("treats malformed configuration responses as unavailable", async () => {
    const panel = searchPanel(async () => ({title:"Agent without config"}));

    await ensureSearchConfiguration(panel);

    expect(panel._settingsSearchConfig).toBeNull();
    expect(panel._settingsSearchConfigAgentId).toBeNull();
    expect(panel._settingsSearchConfigError).toBe(true);
  });
});
