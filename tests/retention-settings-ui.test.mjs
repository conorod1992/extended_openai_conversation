import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const routeSource = await readFile(frontend("management-route.js"), "utf8");
assert.match(routeSource, /"usage-maintenance\/retention": \(\) => import\("\.\/retention-settings-ui\.js"\)/);
assert.doesNotMatch(
  routeSource.match(/const CONFIG_VIEWS = new Set\(\[([\s\S]*?)\]\);/)?.[1] || "",
  /usage-maintenance\/retention/,
  "Retention must not pull in the generic agent-config route",
);

const {renderRetentionSettings, bindRetentionSettings} = await import(frontend("retention-settings-ui.js"));

const panel = {
  _configDirty: false,
  _configurationSaving: false,
  _draft: {
    usage_request_retention_days: 30,
    usage_run_retention_days: 90,
  },
  _result: {
    config: {
      usage_request_retention_days: 30,
      usage_run_retention_days: 90,
    },
    options: {
      usage_request_retention_days: [
        {value: 0, label: "Disabled"},
        {value: 30, label: "30 days"},
      ],
      usage_run_retention_days: [
        {value: 30, label: "30 days"},
        {value: 90, label: "90 days"},
      ],
    },
  },
  _e: String,
};

const markup = renderRetentionSettings(panel);
assert.match(markup, /Retention periods/);
assert.match(markup, /data-retention-config="usage_request_retention_days"/);
assert.match(markup, /data-retention-config="usage_run_retention_days"/);
assert.doesNotMatch(markup, /Chat model|Function Tool|Import configuration/);

let dirtyArgs = null;
let rerendered = false;
const requestControl = {
  dataset: {retentionConfig: "usage_request_retention_days"},
  value: "0",
  addEventListener(type, handler) { if (type === "change") this.handler = handler; },
};
const runControl = {
  dataset: {retentionConfig: "usage_run_retention_days"},
  value: "90",
  addEventListener(type, handler) { if (type === "change") this.handler = handler; },
};
const anchor = {insertAdjacentHTML() {}};
panel._configData = {
  title: "Agent",
  config: {
    usage_request_retention_days: 30,
    usage_run_retention_days: 90,
  },
};
panel._draftTitle = "Agent";
panel._agentId = "agent-a";
panel._draftAgentId = "agent-a";
panel._eocDirtyConfigKeys = new Set();
panel._setConfigDirty = (value) => { panel._configDirty = value; };
panel._render = () => { rerendered = true; };
panel.shadowRoot = {
  querySelectorAll(selector) {
    return selector === "[data-retention-config]" ? [requestControl, runControl] : [];
  },
  querySelector(selector) {
    if (selector === "#save-bar-anchor") return anchor;
    return null;
  },
  dispatchEvent() {},
};

bindRetentionSettings(panel);
requestControl.handler();
assert.equal(panel._draft.usage_request_retention_days, 0);
assert.equal(panel._configDirty, true);
assert.equal(panel._eocDirtyConfigKeys.has("usage_request_retention_days"), true);
assert.equal(rerendered, false);
