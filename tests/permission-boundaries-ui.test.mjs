import assert from "node:assert/strict";

import {
  isRestrictedManagementView,
  nonAdminOverviewKnowledgeSnapshot,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-permission-boundaries.js";

assert.equal(isRestrictedManagementView("data-memory", "knowledge"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", "usage"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", "diagnostics"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", null), true);
assert.equal(isRestrictedManagementView("data-memory", "memories"), false);

globalThis.window = {
  location: {pathname: "/extended-openai/overview"},
  addEventListener() {},
  removeEventListener() {},
};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = {
      hasChildNodes: () => false,
      querySelector: () => null,
      querySelectorAll: () => [],
    };
  }
};
let definedPanel;
globalThis.customElements = {
  define(_name, constructor) { definedPanel = constructor; },
  get() { return definedPanel; },
  whenDefined() { return Promise.resolve(); },
};

const {ExtendedOpenAIManagementPanel} = await import(
  "../custom_components/extended_openai_conversation_responses/frontend/management-panel.js"
);
const panel = new ExtendedOpenAIManagementPanel();
panel._data = {
  is_admin: false,
  agents: [{subentry_id: "agent-1", knowledge_source_count: 3}],
};
panel._agentId = "agent-1";
panel._page = "overview";
panel._subsection = null;

assert.equal(panel._canAccessView("data-memory", "knowledge"), false);
assert.equal(panel._canAccessView("usage-maintenance", "usage"), false);
assert.equal(panel._canAccessView("usage-maintenance", "diagnostics"), false);
assert.equal(panel._canAccessView("data-memory", "memories"), true);
assert.deepEqual(await panel._call("knowledge", "list"), {
  sources: [],
  stats: {source_count: 3},
});

panel._page = "data-memory";
panel._subsection = "knowledge";
panel._request = async (section, action) => ({section, action, original: true});
assert.deepEqual(await panel._call("knowledge", "list"), {
  section: "knowledge",
  action: "list",
  original: true,
});
panel._data.is_admin = true;
assert.equal(panel._canAccessView("data-memory", "knowledge"), true);

assert.deepEqual(nonAdminOverviewKnowledgeSnapshot(panel), {
  sources: [],
  stats: {source_count: 3},
});
