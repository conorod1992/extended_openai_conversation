import assert from "node:assert/strict";
import test from "node:test";
import {bindBackupTransfer, cancelBackupImport, openBackupPicker, refreshImportPreview} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
import {cleanupStateSafety, confirmStateSafeNavigation} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";
import {deferred} from "./frontend-test-helpers.mjs";
import {readFile} from "node:fs/promises";

function harness(bind = bindBackupTransfer) {
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
    shadowRoot:{querySelector:selector => nodes.get(selector) || null,
      querySelectorAll:selector => [".transfer-restore-section", ".transfer-custom-section"].includes(selector) ? [section] : []},
    _hass:{async callWS(message) {
      calls.push(message);
      if (message.action === "import_start") return {session_id:message.subentry_id, chunk_size:1, chunk_count:1};
      assert.equal(message.data.session_id, message.subentry_id, "every session request uses its immutable owner");
      if (message.action === "import_chunk") return {session_id:message.data.session_id, received:1, next_index:1};
      if (message.action === "import_inspect" && message.data.inspect_only) return {valid:true, title:message.subentry_id, available_sections:["persistent_memory"], preview_token:null};
      if (message.action === "import_inspect") return {preview:{selected_sections:["persistent_memory"]}, preview_token:"validated"};
      if (message.action === "import_cancel") return {cancelled:true};
      if (message.action === "import_restore") return {status:"restored"};
      throw new Error(`Unexpected ${message.action}`);
    }},
  };
  bind(panel);
  return {panel, calls, nodes, switchAgent() { agent = {entry_id:"entry-B", subentry_id:"agent-B"}; },
    upload:() => nodes.get("#backup-file-transfer").handlers.change({target:{files:[new Blob(["x"])]}}),
  };
}

async function restoreAcknowledgementWitness(fault, bind = bindBackupTransfer) {
  const h = harness(bind), messages = [], warnings = [];
  await h.upload();
  h.panel._eocBackupTransferCleanup();
  h.panel._backupTransferSession = Object.freeze({session_id:"agent-A", owner:Object.freeze({entry_id:"entry-A", subentry_id:"agent-A"})});
  h.panel._backupTransferPreviewToken = "validated";
  h.nodes.get("#restore-transfer-apply").disabled = false;
  const section = h.nodes.get("#restore-transfer-sections").querySelectorAll()[0];
  section.value = "configuration";
  const persisted = {model:"original"};
  let writes = 0;
  const original = h.panel._hass.callWS;
  h.panel._hass.callWS = async message => {
    if (message.action !== "import_restore") return original(message);
    writes += 1;
    persisted.model = "restored";
    return {status:"restored", provider_connection_message:"Provider credentials were preserved"};
  };
  h.panel._draft = {model:"old local draft"};
  h.panel._clearConfigDraft = () => { h.panel._draft = null; };
  const fail = label => { throw new Error(`${label} failed after commit`); };
  h.panel._toast = (message, warning) => { messages.push(message); warnings.push(warning); if (fault === "toast") fail("toast"); };
  h.panel._invalidateAfterMutation = () => { if (fault === "cache") fail("cache"); };
  h.panel._invalidateCleanConfiguration = () => { if (fault === "configuration cache") fail("configuration cache"); };
  h.panel._loadAgents = async () => { if (fault === "refresh") fail("refresh"); };
  h.panel._setSaving = (_button, saving) => { if (!saving && fault === "saving cleanup") fail("saving cleanup"); };
  if (fault === "modal close") h.nodes.get("#restore-dialog").close = () => fail("modal close");

  await h.nodes.get("#restore-transfer-apply").handlers.click();
  assert.equal(persisted.model, "restored");
  assert.equal(writes, 1);
  assert.equal(h.panel._backupTransferSession, null, "a committed restore must consume its session before presentation");
  assert.equal(h.panel._backupTransferPreviewToken, null, "a committed restore must retire its preview before presentation");
  assert.equal(h.panel._draft, null, "a replaced configuration must retire its old local draft before presentation");
  assert.equal(messages.some(message => message.includes("Unable to restore")), false, "presentation must not reject a committed restore");
  assert.ok(messages.some(message => message.includes("restored")));
  assert.ok(warnings.every(Boolean), "provider-connection and presentation warnings retain warning severity");
  await h.nodes.get("#restore-transfer-apply").handlers.click();
  assert.equal(writes, 1, "presentation failure must not replay a committed restore");
}

for (const failure of [new Error("offline"), {type:"result", success:false, error:{code:3, message:"Connection lost"}}, 3, {code:"invalid_request", message:"Preview rejected"}]) {
  test(`restore transport distinguishes unknown outcomes from server rejection: ${JSON.stringify(failure)}`, async () => {
    const h = harness(), messages = [];
    await h.upload(); h.panel._eocBackupTransferCleanup();
    h.panel._backupTransferSession = {session_id:"agent-A", owner:{entry_id:"entry-A", subentry_id:"agent-A"}};
    h.panel._backupTransferPreviewToken = "validated";
    h.nodes.get("#restore-transfer-apply").disabled = false;
    h.panel._toast = message => messages.push(message);
    let writes = 0;
    h.panel._hass.callWS = async message => {
      if (message.action === "import_restore") {
        if (!failure?.code) writes += 1;
        throw failure;
      }
      return {cancelled:true};
    };
    await h.nodes.get("#restore-transfer-apply").handlers.click();
    assert.equal(messages.some(message => message.includes("Selected sections restored")), false);
    assert.equal(messages.some(message => message.includes("unknown") && message.includes("Refresh")), !failure?.code);
    assert.equal(writes, failure?.code ? 0 : 1);
    assert.equal(h.panel._backupTransferSession, null);
    assert.equal(h.nodes.get("#restore-transfer-apply").disabled, true);
    await h.nodes.get("#restore-transfer-apply").handlers.click();
    assert.equal(writes, failure?.code ? 0 : 1);
  });
}

for (const fault of ["cache", "configuration cache", "modal close", "refresh", "toast", "saving cleanup"]) {
  test(`restore acknowledgement survives ${fault} failure without a second write`, () => restoreAcknowledgementWitness(fault));
}

for (const response of [null, {}, {status:"pending"}, {restored:true}]) {
  test(`unconfirmed restore response remains uncertain: ${JSON.stringify(response)}`, async () => {
    const h = harness(), messages = [];
    await h.upload(); h.panel._eocBackupTransferCleanup();
    h.panel._backupTransferSession = {session_id:"agent-A", owner:{entry_id:"entry-A", subentry_id:"agent-A"}};
    h.panel._backupTransferPreviewToken = "validated";
    h.nodes.get("#restore-transfer-apply").disabled = false;
    h.panel._toast = message => messages.push(message);
    h.panel._hass.callWS = async message => message.action === "import_restore" ? response : {cancelled:true};
    await h.nodes.get("#restore-transfer-apply").handlers.click();
    assert.equal(messages.some(message => message.includes("Selected sections restored")), false);
    assert.ok(messages.some(message => message.includes("unknown") && message.includes("Refresh")));
    assert.equal(h.panel._backupTransferSession, null);
    assert.equal(h.nodes.get("#restore-transfer-apply").disabled, true);
  });
}

test("restore acknowledgement sensitivity detects cache errors escaping the transport boundary", async () => {
  const url = new URL("../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js", import.meta.url);
  const source = await readFile(url, "utf8");
  const guarded = 'processMutationResponse(result, () => panel._invalidateAfterMutation?.(agent.subentry_id, "backup", "restore"))';
  assert.equal(source.split(guarded).length, 2);
  const mutant = source.replace(guarded, 'panel._invalidateAfterMutation?.(agent.subentry_id, "backup", "restore")')
    .replace(/(from\s+["'])(\.[^"']+)(["'])/g, (_match, before, path, after) => before + new URL(path, url).href + after);
  const {bindBackupTransfer: bind} = await import(`data:text/javascript;base64,${Buffer.from(mutant).toString("base64")}`);
  await assert.rejects(() => restoreAcknowledgementWitness("cache", bind), /consume its session|retire its preview|presentation must not reject/);
});

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
  restore.resolve({status:"restored"}); await applying;
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
    restore.resolve({status:"restored"}); await applying;
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
  restore.resolve({status:"restored"}); await applying;
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
    restore.resolve({status:"restored"}); await applying;
    assert.deepEqual(reloads, [{id:"agent-A", owner:{agentId:"agent-A", entryId:"entry-A"}}]);
    assert.equal(h.calls.some(call => call.action === "import_cancel"), false);
    assert.equal(toasts.some(message => message.includes("Unable to restore")), false);
    if (refreshFails) assert.ok(toasts.some(message => message.includes("restored") && message.includes("Refresh offline")));
  });
}
