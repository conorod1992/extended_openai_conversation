import assert from "node:assert/strict";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

globalThis.window = {location:{pathname:"/extended-openai/assistant/basics"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() {return null;}, setItem() {}};
globalThis.HTMLElement = class {constructor() {this.style={};} attachShadow() {this.shadowRoot = new TestEventTarget();}};
globalThis.customElements = {define() {}, get() {}};
globalThis.CSS = {escape:value => value};

export const {ExtendedOpenAIManagementPanel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
export const {saveConfiguration, saveConfigurationAcrossRestart} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-actions.js");
export const {discardConfigurationDraft} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js");
export const {captureConfigurationSubmission, configurationSubmissionIsCurrent, reconcileConfigurationAcknowledgement} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js");
export const {retainPanelDrafts, restorePanelDrafts, stopDraftRecovery} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-draft-lifecycle.js");
export const {confirmStateSafeNavigation} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js");
export const {updateConfigurationControl} = await import("../custom_components/extended_openai_conversation_responses/frontend/configuration-inputs.js");

let identity = 0;
export const copy = value => structuredClone(value);
export const baseConfig = {prompt:"Original", max_tokens:100, temperature:0.4, usage_request_retention_days:30, functions:[], function_groups:[]};
export async function flushUntil(predicate, message = "ownership sequence settled") {
  for (let step = 0; step < 200; step++) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.ok(predicate(), message);
}

export function fixture() {
  const id = ++identity, user = `user-${id}`, entry = `entry-${id}`;
  const agents = ["A", "B"].map(name => ({entry_id:entry, subentry_id:`${name}-${id}`, title:`Saved ${name}`}));
  const records = new Map(agents.map(agent => [agent.subentry_id, {title:agent.title, revision:"r0", config:copy(baseConfig)}]));
  const state = {user, entry, agents, records, calls:[], writes:[], writeGate:null, readGate:null};
  state.snapshot = agent => copy(records.get(agent));
  state.holdWrite = outcome => {
    const gate = {entered:false, outcome, release:deferred()};
    state.writeGate = gate;
    return gate;
  };
  state.holdRead = () => {
    const gate = {entered:false, release:deferred()};
    state.readGate = gate;
    return gate;
  };
  const create = (agent = agents[0].subentry_id, userId = user, projection = "full") => {
    const panel = new ExtendedOpenAIManagementPanel();
    Object.assign(panel, {isConnected:true, _agentId:agent, _draftAgentId:agent,
      _page:"assistant", _subsection:"basics", _data:{is_admin:true, agents:copy(agents)},
      _scopeId:`user:${userId}`, _render() {}, _toast(message) {this.toasts.push(message);},
      toasts:[], _setSaving() {}, _confirm:async () => true,
    });
    panel.shadowRoot.querySelector = () => null;
    panel.shadowRoot.querySelectorAll = () => [];
    panel._hass = {user:{id:userId}, callWS:async request => {
      state.calls.push(copy(request));
      const current = records.get(request.subentry_id);
      if (request.section === "configuration" && ["get", "retention_get"].includes(request.action)) {
        const snapshot = state.snapshot(request.subentry_id), gate = state.readGate;
        if (gate) {
          state.readGate = null; gate.entered = true;
          const outcome = await gate.release.promise;
          if (outcome === "failure") throw new Error("Controlled read failure");
        }
        return request.action === "retention_get" ? {...snapshot, projection:"retention", config:{usage_request_retention_days:snapshot.config.usage_request_retention_days}} : snapshot;
      }
      if (request.section === "configuration" && request.action === "save") {
        state.writes.push(copy(request));
        const gate = state.writeGate;
        if (gate) {
          state.writeGate = null; gate.entered = true;
          await gate.release.promise;
          if (gate.outcome !== "success") throw Object.assign(new Error(gate.outcome === "conflict" ? "Configuration changed in another tab" : "Controlled write failure"), {name:gate.outcome === "cancel" ? "AbortError" : "Error"});
        }
        assert.equal(request.revision, current.revision, "adapter rejects an obsolete write revision");
        current.config = {...current.config, ...copy(request.config)};
        if (Object.hasOwn(request, "title")) current.title = request.title;
        current.revision = `r${Number(current.revision.slice(1)) + 1}`;
        return {valid:true, ...state.snapshot(request.subentry_id)};
      }
      if (request.section === "tools" && request.action === "set_enabled") {
        state.writes.push(copy(request));
        assert.equal(request.revision, current.revision);
        current.revision = `r${Number(current.revision.slice(1)) + 1}`;
        return {functions:[], function_groups:[], revision:current.revision};
      }
      throw new Error(`Unexpected adapter request: ${JSON.stringify(request)}`);
    }};
    panel._configData = state.snapshot(agent);
    if (projection === "retention") {
      panel._page="usage-maintenance"; panel._subsection="retention";
      panel._configData.projection="retention";
      panel._configData.config={usage_request_retention_days:panel._configData.config.usage_request_retention_days};
    }
    panel._draft=copy(panel._configData.config); panel._draftTitle=panel._configData.title;
    panel._result=panel._configData;
    panel._setConfigDirty(false);
    return panel;
  };
  return {state, create, agentA:agents[0].subentry_id, agentB:agents[1].subentry_id};
}

export function edit(panel, key, value) {
  const control = {dataset:{config:key, ...(typeof value === "number" ? {type:"number"} : {})}, value};
  updateConfigurationControl(panel, control);
}

export function visit(panel, page, section) {
  panel._loadToken++;
  panel._page=page; panel._subsection=section;
}

export function replace(source, create, projection = "full") {
  source.isConnected=false;
  retainPanelDrafts(source);
  stopDraftRecovery(source);
  const target=create(source._agentId, source.hass.user.id, projection);
  restorePanelDrafts(target);
  stopDraftRecovery(target);
  return target;
}

export function retire(...panels) {
  for (const panel of panels) {
    stopDraftRecovery(panel);
    panel._setConfigDirty(false);
    panel._eocAcknowledgedConfiguration=null;
    retainPanelDrafts(panel);
  }
}
