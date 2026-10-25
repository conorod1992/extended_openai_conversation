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

const {recoverDisconnectedEditor, confirmDialogClose} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js");
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

test("a superseding Function editor is never overwritten by stale recovery", async () => {
  const old = source(), current = panel();
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  openRecoveredTool = async value => {value._toolEditorLoad = {}; open(value); await gate;};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  await recoverDisconnectedEditor(current, old);
  current.dialog.close();
  current._toolEditorLoad = {}; // The real Function opener allocates a new generation.
  open(current);
  current.textarea.value = "a new manually authored draft";
  release();
  await first;
  await settle();
  assert.equal(current.textarea.value, "a new manually authored draft");
  assert.equal(old.dialog.open, true);
});

test("an acknowledged source cannot be resurrected while preparation awaits", async () => {
  const old = source(), current = panel();
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  openRecoveredTool = async value => {await gate; open(value);};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  old.dialog.close();
  release();
  await first;
  await settle();
  assert.equal(current.textarea.value, "saved");
  assert.equal(old.dialog.open, false);
});

test("returning to the same context retries after an older visit was superseded", async () => {
  const old = source(), current = panel();
  current._loadToken = 1;
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {
    const generation = value._toolEditorLoad = {};
    open(value);
    if (++opens === 1) {
      await gate;
      if (value._toolEditorLoad !== generation) return;
    }
  };
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current.dialog.close();
  current._toolEditorLoad = null;
  current._loadToken = 2;
  await recoverDisconnectedEditor(current, old);
  release();
  await first;
  await settle();
  assert.equal(opens, 2);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(old.dialog.open, false);
});

test("a delayed HA chooser setup cannot open in a different assistant", async () => {
  const old = source(), current = panel();
  old.dialog.id = "ha-llm-tools-dialog";
  old._eocDialogBaselines = new Map([[old.dialog.id, []]]);
  let release;
  current._ensureHALlmTools = () => new Promise(resolve => {release = resolve;});
  let opens = 0;
  current._openHALlmTools = async () => {opens += 1; open(current);};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current._agentId = "another-agent";
  release();
  await first;
  assert.equal(opens, 0);
  assert.equal(old.dialog.open, true);
});

test("a lazy recovery opener does not replace a dialog opened during preparation", async () => {
  const old = source(), current = panel();
  old.dialog.id = "ha-llm-tools-dialog";
  old._eocDialogBaselines = new Map([[old.dialog.id, []]]);
  let release;
  current._ensureHALlmTools = () => new Promise(resolve => {release = resolve;});
  let opens = 0;
  current._openHALlmTools = async () => {opens += 1; open(current);};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  open(current);
  current.textarea.value = "work in another editor";
  release();
  await first;
  assert.equal(opens, 0);
  assert.equal(current.textarea.value, "work in another editor");
  assert.equal(old.dialog.open, true);
});

test("an in-place refresh retires only its unchanged recovery editor and retries", async () => {
  const old = source(), current = panel();
  current._loadToken = 1;
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {
    value._toolEditorLoad = {};
    open(value);
    if (++opens === 1) await gate;
  };
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current._loadToken = 2;
  await recoverDisconnectedEditor(current, old);
  release();
  await first;
  await settle();
  assert.equal(opens, 2);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(old.dialog.open, false);
});

test("refresh cleanup preserves edits made in the receiving editor", async () => {
  const old = source(), current = panel();
  current._loadToken = 1;
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {value._toolEditorLoad = {}; opens += 1; open(value); await gate;};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current._loadToken = 2;
  current.textarea.value = "a new draft owned by the receiving editor";
  await recoverDisconnectedEditor(current, old);
  release();
  await first;
  await settle();
  assert.equal(opens, 1);
  assert.equal(current.dialog.open, true);
  assert.equal(current.textarea.value, "a new draft owned by the receiving editor");
  assert.equal(old.dialog.open, true);
});

test("failed lazy preparation leaves a newer dialog alone", async () => {
  const old = source(), current = panel();
  old.dialog.id = "ha-llm-tools-dialog";
  old._eocDialogBaselines = new Map([[old.dialog.id, []]]);
  let reject;
  current._ensureHALlmTools = () => new Promise((_resolve, fail) => {reject = fail;});
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  open(current);
  current.textarea.value = "a newer manually opened draft";
  reject(new Error("Chooser initialization failed"));
  await first;
  assert.equal(current.dialog.open, true);
  assert.equal(current.textarea.value, "a newer manually opened draft");
  assert.equal(old.dialog.open, true);
  assert.equal(Boolean(old._eocRecoveringEditor), false);
});

test("a failed cleanup read releases recovery ownership without discarding either draft", async () => {
  const old = source(), current = panel();
  current._loadToken = 1;
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  openRecoveredTool = async value => {value._toolEditorLoad = {}; open(value); await gate;};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current._loadToken = 2;
  Object.defineProperty(current.textarea, "value", {get() {throw new Error("Draft temporarily unavailable");}});
  release();
  await assert.rejects(first, /temporarily unavailable/);
  assert.equal(Boolean(old._eocRecoveringEditor), false);
  assert.equal(Boolean(current._eocReceivingEditor), false);
  assert.equal(old.dialog.open, true);
  assert.equal(current.dialog.open, true);
});

for (const returnBeforeReady of [false, true]) {
  test(`Cancel after refresh defers recovery, with later visit=${returnBeforeReady}`, async () => {
    const old = source(), current = panel();
    current._loadToken = 1;
    let release;
    const gate = new Promise(resolve => {release = resolve;});
    let opens = 0;
    openRecoveredTool = async value => {value._toolEditorLoad = {}; open(value); if (++opens === 1) await gate;};
    const first = recoverDisconnectedEditor(current, old);
    await settle();
    current._loadToken = 2;
    await recoverDisconnectedEditor(current, old);
    await confirmDialogClose(current, current.dialog);
    current._toolEditorLoad = null;
    if (returnBeforeReady) {
      current._loadToken = 3;
      await recoverDisconnectedEditor(current, old);
    }
    release();
    await first;
    await settle();
    assert.equal(opens, returnBeforeReady ? 2 : 1);
    assert.equal(current.dialog.open, returnBeforeReady);
    assert.equal(old.dialog.open, !returnBeforeReady);
    if (!returnBeforeReady) {
      current._loadToken = 3;
      await recoverDisconnectedEditor(current, old);
      assert.equal(current.textarea.value, "original unsaved YAML");
    }
  });
}

test("declining a close confirmation does not defer recovery", async () => {
  const old = source(), current = panel();
  current._loadToken = 1;
  let release;
  const gate = new Promise(resolve => {release = resolve;});
  let opens = 0;
  openRecoveredTool = async value => {value._toolEditorLoad = {}; open(value); if (++opens === 1) await gate;};
  const first = recoverDisconnectedEditor(current, old);
  await settle();
  current._loadToken = 2;
  await recoverDisconnectedEditor(current, old);
  current.textarea.value = "a receiving draft";
  current._confirm = async () => false;
  assert.equal(await confirmDialogClose(current, current.dialog), false);
  assert.equal(current.dialog.open, true);
  current.textarea.value = "saved";
  release();
  await first;
  await settle();
  assert.equal(opens, 2);
  assert.equal(current.textarea.value, "original unsaved YAML");
  assert.equal(old.dialog.open, false);
});
