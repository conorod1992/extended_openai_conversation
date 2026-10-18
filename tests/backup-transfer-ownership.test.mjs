import assert from "node:assert/strict";
import test from "node:test";
import {bindBackupTransfer, cancelBackupImport, openBackupPicker, refreshImportPreview} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
import {cleanupStateSafety, confirmStateSafeNavigation} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";
import {deferred} from "./frontend-test-helpers.mjs";

function harness() {
  let agent = {entry_id:"entry-A", subentry_id:"agent-A"};
  const calls = [], nodes = new Map();
  const section = {value:"persistent_memory", checked:true, addEventListener(){}};
  for (const id of ["backup-file-transfer", "restore-backup-transfer", "restore-transfer-apply", "restore-transfer-cancel", "restore-backup-name", "restore-backup-meta", "restore-summary", "restore-transfer-sections", "restore-transfer-status", "restore-dialog"]) {
    nodes.set(`#${id}`, {handlers:{}, dataset:{}, disabled:false, open:false, innerHTML:"", textContent:"",
      addEventListener(type, handler) { this.handlers[type] = handler; },
      querySelectorAll() { return [section]; },
      showModal() { this.open = true; },
      close() { this.open = false; this.handlers.close?.(); },
    });
  }
  const panel = {_selectedAgent:() => agent, _viewKey:() => "usage-maintenance/backup-restore", _e:String,
    _setSaving(){}, _toast(){}, _clearConfigDraft(){ assert.fail("must not clear another agent's draft"); },
    _loadAgents:async()=>{},
    shadowRoot:{querySelector:selector => nodes.get(selector) || null, querySelectorAll:() => [section]},
    _hass:{async callWS(message) {
      calls.push(message);
      if (message.action === "import_start") return {session_id:message.subentry_id, chunk_size:1, chunk_count:1};
      assert.equal(message.data.session_id, message.subentry_id, "every session request uses its immutable owner");
      if (message.action === "import_chunk") return {session_id:message.data.session_id, received:1, next_index:1};
      if (message.action === "import_inspect" && message.data.inspect_only) return {valid:true, title:message.subentry_id, available_sections:["persistent_memory"], preview_token:null};
      if (message.action === "import_inspect") return {preview:{selected_sections:["persistent_memory"]}, preview_token:"validated"};
      if (message.action === "import_cancel") return {cancelled:true};
      if (message.action === "import_restore") return {restored:true};
      throw new Error(`Unexpected ${message.action}`);
    }},
  };
  bindBackupTransfer(panel);
  return {panel, calls, nodes, switchAgent() { agent = {entry_id:"entry-B", subentry_id:"agent-B"}; },
    upload:() => nodes.get("#backup-file-transfer").handlers.change({target:{files:[new Blob(["x"])]}}),
  };
}

for (const cleanup of ["cancel", "picker", "preview", "navigation", "disconnect", "escape", "close"]) {
  test(`prepared imports retain ownership during ${cleanup} after switching agents`, async () => {
    const h = harness();
    await h.upload();
    const session = h.panel._backupTransferSession;
    assert.equal(Object.isFrozen(session), true);
    assert.equal(Object.isFrozen(session.owner), true);
    h.switchAgent();
    if (cleanup === "cancel") await cancelBackupImport(h.panel);
    if (cleanup === "picker") openBackupPicker(h.panel, {value:"", click(){}});
    if (cleanup === "preview") assert.equal(refreshImportPreview(h.panel), false);
    if (cleanup === "navigation") assert.equal(await confirmStateSafeNavigation(h.panel, null), true);
    if (cleanup === "disconnect") cleanupStateSafety(h.panel);
    if (cleanup === "escape") { h.nodes.get("#restore-dialog").handlers.cancel(); h.nodes.get("#restore-dialog").close(); }
    if (cleanup === "close") h.nodes.get("#restore-dialog").close();
    assert.equal(h.panel._backupTransferSession, null);
    const cancellations = h.calls.filter(call => call.action === "import_cancel");
    assert.ok(cancellations.some(call => call.entry_id === "entry-A" && call.subentry_id === "agent-A"));
    h.panel._eocBackupTransferCleanup();
  });
}

test("choosing a new backup after an agent switch retires the old owned session", async () => {
  const h = harness();
  await h.upload(); h.switchAgent(); await h.upload();
  assert.equal(h.panel._backupTransferSession.owner.subentry_id, "agent-B");
  assert.ok(h.calls.some(call => call.action === "import_cancel" && call.subentry_id === "agent-A"));
  h.panel._eocBackupTransferCleanup();
});

test("agent switching retires an upload before its pending chunk completes", async () => {
  const h = harness(), chunk = deferred(), started = deferred();
  const original = h.panel._hass.callWS;
  h.panel._hass.callWS = message => {
    if (message.action === "import_chunk") { started.resolve(); return chunk.promise; }
    return original(message);
  };
  const upload = h.upload(); await started.promise;
  h.switchAgent(); await confirmStateSafeNavigation(h.panel, null);
  chunk.resolve({session_id:"agent-A", received:1, next_index:1});
  await upload;
  assert.equal(h.panel._backupTransferSession, null);
  assert.ok(h.calls.some(call => call.action === "import_cancel" && call.subentry_id === "agent-A"));
  assert.equal(h.calls.some(call => call.action === "import_inspect"), false);
});

test("a switched agent cannot apply a prepared restore after confirmation", async () => {
  const h = harness(); await h.upload();
  h.panel._backupTransferPreviewToken = "validated";
  h.nodes.get("#restore-transfer-apply").disabled = false;
  h.panel._confirm = async () => { await confirmStateSafeNavigation(h.panel, null); h.switchAgent(); return true; };
  await h.nodes.get("#restore-transfer-apply").handlers.click();
  assert.equal(h.calls.some(call => call.action === "import_restore"), false);
  assert.equal(h.panel._backupTransferSession, null);
});

for (const switchAgent of [false, true]) test(`late restore completion cannot clear a newer prepared import (switch agent: ${switchAgent})`, async () => {
  const h = harness(); await h.upload();
  h.panel._backupTransferPreviewToken = "validated";
  h.nodes.get("#restore-transfer-apply").disabled = false;
  const restore = deferred(), started = deferred(), original = h.panel._hass.callWS;
  h.panel._hass.callWS = message => {
    if (message.action === "import_restore") { assert.equal(message.subentry_id, "agent-A"); started.resolve(); return restore.promise; }
    return original(message);
  };
  const applying = h.nodes.get("#restore-transfer-apply").handlers.click(); await started.promise;
  await confirmStateSafeNavigation(h.panel, null);
  if (switchAgent) h.switchAgent();
  await h.upload();
  const next = h.panel._backupTransferSession;
  h.panel._loadAgents = async () => assert.fail("a newer Backup dialog must retain its own presentation");
  restore.resolve({restored:true}); await applying;
  assert.equal(h.panel._backupTransferSession, next);
  assert.equal(h.panel._backupTransferInspection.title, switchAgent ? "agent-B" : "agent-A");
  h.panel._eocBackupTransferCleanup();
});

for (const dismissal of ["escape", "cancel"]) {
  test(`an executing restore survives ${dismissal} and reports completion`, async () => {
    const h = harness(); await h.upload();
    await new Promise(resolve => setTimeout(resolve, 200));
    const restore = deferred(), started = deferred(), original = h.panel._hass.callWS;
    h.panel._hass.callWS = message => {
      if (message.action === "import_restore") { started.resolve(); return restore.promise; }
      return original(message);
    };
    const toasts = [], reloads = [];
    h.panel._toast = message => toasts.push(message);
    h.panel._loadAgents = async (id, owner) => {
      assert.deepEqual(owner, {agentId:"agent-A", entryId:"entry-A"});
      reloads.push(id);
    };
    const session = h.panel._backupTransferSession;
    const applying = h.nodes.get("#restore-transfer-apply").handlers.click(); await started.promise;
    if (dismissal === "escape") {
      let prevented = false;
      h.nodes.get("#restore-dialog").handlers.cancel({preventDefault() { prevented = true; }});
      assert.equal(prevented, true, "Escape must not dismiss a restore that may already be committed");
    } else {
      assert.equal(h.nodes.get("#restore-transfer-cancel").disabled, true);
      h.nodes.get("#restore-transfer-cancel").handlers.click();
    }
    assert.equal(h.panel._backupTransferSession, session);
    assert.equal(h.calls.some(call => call.action === "import_cancel"), false);
    restore.resolve({restored:true}); await applying;
    assert.equal(h.panel._backupTransferSession, null);
    assert.equal(reloads.length, 1);
    assert.ok(toasts.some(message => message.includes("restored")));
    assert.equal(h.nodes.get("#restore-dialog").open, false);
  });
}

test("a departed restore reports its outcome and invalidates its owner's cache", async () => {
  const h = harness(); await h.upload();
  await new Promise(resolve => setTimeout(resolve, 200));
  const restore = deferred(), started = deferred(), original = h.panel._hass.callWS;
  h.panel._hass.callWS = message => {
    if (message.action === "import_restore") { started.resolve(); return restore.promise; }
    return original(message);
  };
  const toasts = [], invalidated = [];
  h.panel._toast = message => toasts.push(message);
  h.panel._invalidateCleanConfiguration = id => invalidated.push(id);
  h.panel._loadAgents = async () => assert.fail("must not replace another agent's UI");
  const applying = h.nodes.get("#restore-transfer-apply").handlers.click(); await started.promise;
  await confirmStateSafeNavigation(h.panel, null); h.switchAgent(); await h.upload();
  const next = h.panel._backupTransferSession;
  restore.resolve({restored:true}); await applying;
  assert.equal(h.panel._backupTransferSession, next);
  assert.deepEqual(invalidated, ["agent-A"]);
  assert.ok(toasts.some(message => message.includes("restored") && message.includes("agent-A")));
  h.panel._eocBackupTransferCleanup();
});

for (const refreshFails of [false, true]) {
  test(`background restore refreshes its selected agent after navigation (refresh fails: ${refreshFails})`, async () => {
    const h = harness(); await h.upload();
    await new Promise(resolve => setTimeout(resolve, 200));
    const restore = deferred(), started = deferred(), original = h.panel._hass.callWS;
    h.panel._hass.callWS = message => {
      if (message.action === "import_restore") { started.resolve(); return restore.promise; }
      return original(message);
    };
    let view = "usage-maintenance/backup-restore";
    h.panel._viewKey = () => view;
    const reloads = [], toasts = [];
    h.panel._toast = message => toasts.push(message);
    h.panel._loadAgents = async (id, owner) => {
      reloads.push({id, owner});
      if (refreshFails) throw new Error("Refresh offline");
    };
    const applying = h.nodes.get("#restore-transfer-apply").handlers.click(); await started.promise;
    await confirmStateSafeNavigation(h.panel, "assistant/basics"); view = "assistant/basics";
    restore.resolve({restored:true}); await applying;
    assert.deepEqual(reloads, [{id:"agent-A", owner:{agentId:"agent-A", entryId:"entry-A"}}]);
    assert.equal(h.calls.some(call => call.action === "import_cancel"), false);
    assert.equal(toasts.some(message => message.includes("Unable to restore")), false);
    if (refreshFails) assert.ok(toasts.some(message => message.includes("restored") && message.includes("Refresh offline")));
  });
}
