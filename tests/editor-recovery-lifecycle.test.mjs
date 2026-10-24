import assert from "node:assert/strict";
import {registerHooks} from "node:module";
import {after, test} from "node:test";

// Only the lazy editor module is substituted. The state-safety implementation
// and its YAML adapter are imported unchanged, including the real await points.
let releaseImport;
globalThis.__editorImportGate = new Promise(resolve => {releaseImport = resolve;});
let openRecoveredTool;
globalThis.__openRecoveredTool = panel => openRecoveredTool(panel);
const mock = "await globalThis.__editorImportGate; export const openTool = panel => globalThis.__openRecoveredTool(panel);";
const hooks = registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "./agent-config-tools-base.js" && context.parentURL.endsWith("/management-state-safety.js")) {
      return {url:`data:text/javascript,${encodeURIComponent(mock)}`, shortCircuit:true};
    }
    return next(specifier, context);
  },
});
after(() => {
  hooks.deregister();
  delete globalThis.__editorImportGate;
  delete globalThis.__openRecoveredTool;
});

const {recoverDisconnectedEditor} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js");
const settle = () => new Promise(resolve => setImmediate(resolve));
function panel(connected = true) {
  const textarea = {id:"tool-yaml", type:"textarea", tagName:"TEXTAREA", value:"saved", dispatchEvent() {}};
  const dialog = {id:"tool-dialog", open:false, querySelector:() => textarea, querySelectorAll:() => [textarea], close() {this.open = false;}};
  return {
    isConnected:connected, hass:{user:{id:"user"}}, _agentId:"agent", _scopeId:null,
    _viewKey:() => "capabilities/functions", _selectedAgent:() => ({entry_id:"entry"}),
    _data:{is_admin:true}, _draft:{functions:[{spec:{name:"sample"}}]},
    _toolOriginalName:"sample", _toolInitialYaml:"saved", dialog, textarea,
    shadowRoot:{querySelector(selector) {return selector === "dialog[open]" ? (dialog.open ? dialog : null) : dialog;}},
  };
}
function source() {
  const value = panel(false);
  value.dialog.open = true;
  value.textarea.value = "original unsaved YAML";
  return value;
}
function open(panel) {
  panel.dialog.open = true;
  panel.textarea.value = panel._toolInitialYaml;
}

test("a pending lazy import retries the latest replacement exactly once", async () => {
  const old = source(), abandoned = panel(), current = panel();
  const opened = [];
  openRecoveredTool = value => {opened.push(value); open(value);};
  const first = recoverDisconnectedEditor(abandoned, old);
  assert.equal(Boolean(old._eocRecoveringEditor), true);
  await recoverDisconnectedEditor(current, old);
  await recoverDisconnectedEditor(current, old); // Both lifecycle restore points.
  await recoverDisconnectedEditor(abandoned, old); // An older restore point must not displace the replacement.
  abandoned.isConnected = false;
  releaseImport();
  await first;
  await settle();
  assert.deepEqual(opened, [current]);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(old.dialog.open, false);
  assert.equal(Boolean(current._eocReceivingEditor), false);
  assert.equal(Boolean(old._eocRecoveringEditor), false);
});

for (const change of ["user", "entry", "agent", "route", "scope", "access", "connected-source"]) {
  test(`abandoned recovery does not transfer across ${change}`, async () => {
    const old = source(), abandoned = panel(), current = panel();
    let release;
    const gate = new Promise(resolve => {release = resolve;});
    openRecoveredTool = async value => {if (value === abandoned) await gate; open(value);};
    const first = recoverDisconnectedEditor(abandoned, old);
    await settle();
    abandoned.isConnected = false;
    await recoverDisconnectedEditor(current, old);
    if (change === "user") current.hass.user.id = "other";
    if (change === "entry") current._selectedAgent = () => ({entry_id:"other"});
    if (change === "agent") current._agentId = "other";
    if (change === "route") current._viewKey = () => "guide";
    if (change === "scope") current._scopeId = "other";
    if (change === "access") current._data.is_admin = false;
    if (change === "connected-source") old.isConnected = true;
    release();
    await first;
    await settle();
    assert.equal(current.dialog.open, false);
    assert.equal(old.dialog.open, true);
    assert.equal(old.textarea.value, "original unsaved YAML");
  });
}

test("repeated restore points do not reopen a cancelled current editor", async () => {
  const old = source(), current = panel();
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {opens += 1; open(value); await gate;};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  await recoverDisconnectedEditor(current, old);
  current.dialog.close();
  release();
  await first;
  await settle();
  assert.equal(opens, 1);
  assert.equal(current.dialog.open, false);
  assert.equal(old.dialog.open, true);
  assert.equal(old.textarea.value, "original unsaved YAML");
});

test("two retained sources cannot concurrently replace the receiving editor", async () => {
  const firstSource = source(), secondSource = source(), current = panel();
  secondSource.textarea.value = "a different retained draft";
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {opens += 1; await gate; open(value);};
  const first = recoverDisconnectedEditor(current, firstSource);
  await settle();
  await recoverDisconnectedEditor(current, secondSource);
  release();
  await first;
  await settle();
  assert.equal(opens, 1);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(firstSource.dialog.open, false);
  assert.equal(secondSource.dialog.open, true);
});

test("a failed draft read cannot leave recovery ownership stuck", async () => {
  const old = source(), current = panel();
  let reads = 0;
  Object.defineProperty(old.textarea, "value", {configurable:true, get() {
    if (++reads === 2) throw new Error("Editor temporarily unavailable");
    return "original unsaved YAML";
  }});
  await assert.rejects(recoverDisconnectedEditor(current, old), /temporarily unavailable/);
  assert.equal(Boolean(old._eocRecoveringEditor), false);
  assert.equal(Boolean(current._eocReceivingEditor), false);
  Object.defineProperty(old.textarea, "value", {value:"original unsaved YAML", writable:true});
  openRecoveredTool = open;
  await recoverDisconnectedEditor(current, old);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(old.dialog.open, false);
});
