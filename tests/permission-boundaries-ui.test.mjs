import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

import {
  installManagementPermissionBoundaries,
  isRestrictedManagementView,
  nonAdminOverviewKnowledgeSnapshot,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-permission-boundaries.js";

assert.equal(isRestrictedManagementView("data-memory", "knowledge"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", "usage"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", "diagnostics"), true);
assert.equal(isRestrictedManagementView("usage-maintenance", null), true);
assert.equal(isRestrictedManagementView("data-memory", "memories"), false);

class Panel {
  constructor() {
    this._data = {is_admin: false};
    this._view = "overview";
  }

  _canAccessView() {
    return true;
  }

  _viewKey() {
    return this._view;
  }

  _selectedAgent() {
    return {knowledge_source_count: 3};
  }

  async _call(section, action) {
    return {section, action, original: true};
  }
}

const registry = {
  whenDefined: async () => true,
  get: () => Panel,
};
assert.equal(await installManagementPermissionBoundaries(registry), true);

const panel = new Panel();
assert.equal(panel._canAccessView("data-memory", "knowledge"), false);
assert.equal(panel._canAccessView("usage-maintenance", "usage"), false);
assert.equal(panel._canAccessView("usage-maintenance", "diagnostics"), false);
assert.equal(panel._canAccessView("data-memory", "memories"), true);
assert.deepEqual(await panel._call("knowledge", "list"), {
  sources: [],
  stats: {source_count: 3},
});

panel._view = "data-memory/knowledge";
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

const bootstrap = readFileSync(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
assert.match(bootstrap, /management-permission-boundaries\.js/);
