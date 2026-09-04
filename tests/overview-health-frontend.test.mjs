import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {renderOverview} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js";

const panel = {
  _result: {
    usage: {today: {total_tokens: 0}, month: {total_tokens: 0}},
    conversations: {archive_retention_days: 30},
    load_errors: [],
    setup_health: {
      state: "warning",
      summary: "Review recommended",
      warning_count: 1,
      error_count: 0,
      unknown_count: 0,
      can_manage: true,
      checks: [
        {
          id: "provider_runtime",
          state: "ready",
          title: "Provider runtime",
          value: "openai · gpt-5-mini",
          detail: "API client is loaded. Overview does not make a live provider request.",
          action: {page: "usage-maintenance", subsection: "diagnostics", target: null},
        },
        {
          id: "memory",
          state: "neutral",
          title: "Persistent memory",
          value: "Off by choice",
          detail: "Persistent memory is optional and is currently disabled.",
          action: {page: "data-memory", subsection: "memory-settings", target: null},
        },
        {
          id: "web_search",
          state: "warning",
          title: "Web Search",
          value: "Needs attention",
          detail: "Web Search requires the Responses API.",
          action: {page: "assistant", subsection: "basics", target: "config-api_mode"},
        },
      ],
    },
  },
  _e(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
  },
  _titleCase(value) {
    return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  },
};

const agent = {
  title: "Kitchen Assistant",
  provider: "openai",
  model: "gpt-5-mini",
  memory_mode: "off",
  memory_count: 0,
  knowledge_source_count: 0,
  function_count: 0,
  function_group_count: 0,
  archive_enabled: false,
  guest_mode: {state: "inactive", has_home_assistant_exclusions: false},
};

const markup = renderOverview(panel, agent);
assert.match(markup, /Setup & health/);
assert.match(markup, /Optional features that are off by choice are not treated as problems/);
assert.match(markup, /Off by choice/);
assert.match(markup, /Run live test/);
assert.match(markup, /data-target="config-api_mode"/);
assert.match(markup, /Overview never sends a provider test request/);
assert.match(markup, /1 item to review/);

panel._result.setup_health = {
  ...panel._result.setup_health,
  state: "warning",
  warning_count: 0,
  unknown_count: 1,
  can_manage: false,
  checks: [{
    id: "knowledge",
    state: "unknown",
    title: "Knowledge Library",
    value: "Unable to determine",
    detail: "Overview could not load the source count.",
    action: {page: "data-memory", subsection: "knowledge", target: null},
  }],
};
const nonAdminMarkup = renderOverview(panel, agent);
assert.match(nonAdminMarkup, /Unable to determine/);
assert.match(nonAdminMarkup, /1 item to review/);
assert.doesNotMatch(nonAdminMarkup, /setup-health-action/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js", import.meta.url),
  "utf8",
);
assert.match(source, /_pendingSettingFocus = button\.dataset\.target/);
assert.doesNotMatch(source, /diagnostics.*test_agent/s);
