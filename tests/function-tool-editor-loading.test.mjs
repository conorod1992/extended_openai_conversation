import assert from "node:assert/strict";
import {bindTools, openTool} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js";
import {deferred, TestEventTarget} from "./frontend-test-helpers.mjs";

class Control extends TestEventTarget {
  constructor(id) {
    super();
    this.id = id; this.value = ""; this.dataset = {};
    this.parentElement = {inert: false}; this.attributes = new Map(); this.disabled = false;
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  showModal() { this.open = true; }
  close() { this.open = false; void this.emit("close"); }
  focus() { this.focused = true; }
}
function harness() {
  const ids = ["tool-dialog", "tool-dialog-title", "tool-dialog-meta", "tool-yaml", "tool-error", "built-in-picker", "built-in-function", "tool-save", "tool-validate", "tool-cancel", "add-tool"];
  const controls = Object.fromEntries(ids.map((id) => [id, new Control(id)]));
  const edit = new Control("edit-tool"); edit.dataset.index = "0";
  const calls = [];
  const loadStarted = new Map();
  function nextLoad(action) {
    const started = deferred();
    const pending = loadStarted.get(action) || [];
    pending.push(started);
    loadStarted.set(action, pending);
    return started.promise;
  }
  const panel = {
    _agentId: "agent-a", _e: String, _toast() {}, _render() {}, _syncConfigDirty() {},
    _draft: {functions: [{spec: {name: "example", description: "original"}, function: {type: "native"}}]},
    _configData: {revision: "rev-a", config: {}},
    _setSaving(button, saving) { button.disabled = saving; },
    shadowRoot: {
      querySelector(selector) { return controls[selector.replace(/^#/, "")] || null; },
      querySelectorAll(selector) { return selector === ".edit-tool" ? [edit] : []; },
    },
    async _call(section, action, params) {
      calls.push({section, action, params});
      if (["serialize", "starter", "built_in_catalog"].includes(action)) {
        const load = deferred();
        loadStarted.get(action)?.shift()?.resolve(load);
        return load.promise;
      }
      if (action === "validate_yaml") return {valid: true, config: {spec: {name: "example", description: params.yaml}}, name: "example", type: "native"};
      if (action === "save") return {functions: [params.tool], function_groups: [], revision: "rev-b"};
      throw new Error(`Unexpected ${section}/${action}`);
    },
  };
  bindTools(panel);
  // Exercise the editor lifecycle directly; real-browser collection tests cover
  // delegated dispatch from cards (including current keys after deletions).
  edit.addEventListener("click", () => openTool(panel, 0));
  controls["add-tool"].addEventListener("click", () => openTool(panel));
  return {panel, controls, edit, calls, nextLoad};
}

// A visible dialog is not necessarily ready: no typing, presets or save before hydration.
{
  const {controls: c, edit, calls, nextLoad} = harness();
  const requested = nextLoad("serialize");
  const opened = edit.emit("click");
  assert.equal(c["tool-dialog"].open, true);
  assert.equal(c["tool-yaml"].readOnly, true);
  assert.equal(c["tool-yaml"].parentElement.inert, true, "native editor descendants must also be inert");
  for (const id of ["tool-save", "tool-validate", "built-in-function"]) assert.equal(c[id].disabled, true, id);
  assert.equal(c["tool-cancel"].disabled, false);
  await c["tool-save"].emit("click");
  assert.equal(calls.filter((call) => call.action === "save").length, 0);
  (await requested).resolve({yaml: "original YAML"});
  await opened;
  assert.equal(c["tool-yaml"].value, "original YAML");
  assert.equal(c["tool-yaml"].readOnly, false);
  assert.equal(c["tool-yaml"].parentElement.inert, false);
  assert.equal(c["tool-save"].disabled, false);
  c["tool-yaml"].value = "edited YAML";
  await c["tool-save"].emit("click");
  assert.equal(calls.find((call) => call.action === "save").params.tool.spec.description, "edited YAML");
  assert.equal(c["tool-dialog"].open, false);
}

// Closing and reopening the same DOM must invalidate both old success and error replies.
for (const staleFails of [false, true]) {
  const {controls: c, edit, nextLoad} = harness();
  const firstRequested = nextLoad("serialize");
  const first = edit.emit("click");
  const firstLoad = await firstRequested;
  c["tool-dialog"].close();
  const secondRequested = nextLoad("serialize");
  const second = edit.emit("click");
  (await secondRequested).resolve({yaml: "second session YAML"});
  await second;
  c["tool-yaml"].value = "second session user edit";
  if (staleFails) firstLoad.reject(new Error("old session failed"));
  else firstLoad.resolve({yaml: "STALE YAML"});
  await first;
  assert.equal(c["tool-yaml"].value, "second session user edit");
  assert.equal(c["tool-error"].textContent, "Edit the YAML, then save the function.");
  assert.equal(c["tool-save"].disabled, false);
}

// Loading failures cannot offer a blank replacement; cancellation releases shared controls.
{
  const {controls: c, edit, nextLoad} = harness();
  const requested = nextLoad("serialize");
  const opened = edit.emit("click");
  (await requested).reject(new Error("Unable to load YAML"));
  await opened;
  assert.equal(c["tool-error"].textContent, "Unable to load YAML");
  assert.equal(c["tool-save"].disabled, true);
  assert.equal(c["tool-dialog"].attributes.get("aria-busy"), "false");
  c["tool-dialog"].close();
  assert.equal(c["tool-save"].disabled, false, "a following invalid-tool repair editor must remain usable");
  assert.equal(c["tool-yaml"].readOnly, false);
  assert.equal(c["tool-yaml"].parentElement.inert, false);
}

// Agent changes and dialog replacement invalidate unfinished editor initialization.
for (const change of ["agent", "dialog"]) {
  const {panel, controls: c, edit, nextLoad} = harness();
  const oldEditor = c["tool-yaml"];
  const requested = nextLoad("serialize");
  const opened = edit.emit("click");
  const load = await requested;
  if (change === "agent") panel._agentId = "agent-b";
  else c["tool-dialog"] = new Control("tool-dialog");
  load.resolve({yaml: "wrong session"});
  await opened;
  assert.equal(oldEditor.value, "");
}

// A close event queued before navigation/re-render must not touch newer controls.
for (const change of ["removed", "replaced", "reopened"]) {
  const {panel, controls: c, edit, nextLoad} = harness();
  const oldDialog = c["tool-dialog"];
  const requested = nextLoad("serialize");
  const opened = edit.emit("click");
  const load = await requested;
  const currentToken = panel._toolEditorLoad;
  if (change === "removed") {
    delete c["tool-dialog"];
    delete c["tool-yaml"];
  } else if (change === "replaced") {
    c["tool-dialog"] = new Control("tool-dialog");
  }
  await oldDialog.emit("close");
  assert.equal(panel._toolEditorLoad, currentToken);
  assert.equal(c["tool-save"].disabled, true);
  load.resolve({yaml: "finished"});
  await opened;
}

// The Add path waits for both the starter and built-in catalog, not just one reply.
{
  const {controls: c, nextLoad} = harness();
  const starterRequested = nextLoad("starter");
  const catalogRequested = nextLoad("built_in_catalog");
  const opened = c["add-tool"].emit("click");
  const [starter, catalog] = await Promise.all([starterRequested, catalogRequested]);
  starter.resolve({yaml: "starter YAML"});
  await Promise.resolve();
  assert.equal(c["tool-save"].disabled, true);
  catalog.resolve({functions: []});
  await opened;
  assert.equal(c["tool-yaml"].value, "starter YAML");
  assert.equal(c["tool-save"].disabled, false);
}
console.log("Function Tool editor hydration, stale sessions, failure and save tests passed");
