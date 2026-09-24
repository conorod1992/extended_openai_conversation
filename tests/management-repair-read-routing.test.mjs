import assert from "node:assert/strict";

globalThis.window = {location: {pathname: "/extended-openai/assistant/basics"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {
  attachShadow() { this.shadowRoot = {hasChildNodes: () => false, querySelector: () => null}; }
};
globalThis.customElements = {define() {}, get() { return null; }, whenDefined() { return Promise.resolve(); }};

const {ExtendedOpenAIManagementPanel} = await import(
  "../custom_components/extended_openai_conversation_responses/frontend/management-panel.js"
);
const {startStoredConfigurationPrefetch, AGENT_KEY, ENTRY_KEY} = await import(
  "../custom_components/extended_openai_conversation_responses/frontend/management-route.js"
);

const issue = {field: "functions", repairable: true};
const agent = (repairable) => ({
  entry_id: "entry-1", subentry_id: "agent-1", title: "Assistant",
  ...(repairable ? {configuration_issue: issue} : {}),
});
const response = (action) => ({
  title: "Assistant", revision: "revision-1",
  ...(action === "retention_get" ? {projection: "retention"} : {}),
  config: action === "retention_get"
    ? {usage_request_retention_days: 30, usage_run_retention_days: 90}
    : {chat_model: "gpt-test", functions: []},
  ...(action === "get" ? {function_repair: {invalid_count: 1, invalid_tools: []}} : {}),
});

function panelFor(view, repairable) {
  const panel = new ExtendedOpenAIManagementPanel();
  const [page, subsection] = view.split("/");
  panel._page = page;
  panel._subsection = subsection;
  panel._data = {agents: [agent(repairable)], is_admin: true};
  panel._agentId = "agent-1";
  panel._render = () => {};
  const calls = [];
  panel._hass = {callWS: async (message) => {
    calls.push(message);
    return response(message.action);
  }};
  return {panel, calls};
}

// Before the catalogue is known, the stored-agent prefetch goes straight to
// the normal configuration endpoint and is consumed without a second request.
{
  const storage = new Map([[AGENT_KEY, "agent-1"], [ENTRY_KEY, "entry-1"]]);
  globalThis.localStorage = {getItem(key) { return storage.get(key) ?? null; }, setItem() {}};
  const {panel, calls} = panelFor("assistant/basics", true);
  startStoredConfigurationPrefetch(panel, "agent-1");
  await panel._loadConfigDraft();
  assert.deepEqual(calls.map(({section, action}) => [section, action]), [["configuration", "get"]]);
  assert.equal(panel._result.function_repair.invalid_count, 1);
}

// A fresh panel after leaving and re-entering has catalogue repair metadata,
// but should still use the modern normal read endpoints exactly once.
for (const repairable of [true, false]) {
  for (const [view, action] of [["assistant/basics", "get"], ["usage-maintenance/retention", "retention_get"]]) {
    const {panel, calls} = panelFor(view, repairable);
    await panel._loadConfigDraft();
    assert.deepEqual(calls.map(({section, action}) => [section, action]), [["configuration", action]]);
    if (action === "retention_get") assert.equal(panel._result.projection, "retention");
    else assert.equal(panel._result.function_repair.invalid_count, 1);
  }
}

// Keep repair-aware mutation routing and the Function repair workflow intact.
{
  const {panel, calls} = panelFor("assistant/basics", true);
  await panel._callCore("configuration", "validate", {config: {prompt: "changed"}});
  await panel._callCore("configuration", "save", {config: {prompt: "changed"}});
  await panel._callCore("function_repair", "get");
  assert.deepEqual(calls.map(({section, action}) => [section, action]), [
    ["function_repair", "configuration_validate"],
    ["function_repair", "configuration_save"],
    ["function_repair", "get"],
  ]);
}
