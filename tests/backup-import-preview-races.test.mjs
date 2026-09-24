import assert from "node:assert/strict";
import test from "node:test";

import {cancelBackupImport, openBackupPicker, refreshImportPreview} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor(predicate) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await delay(10);
  }
  assert.fail("Timed out waiting for preview request or result");
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function previewHarness() {
  const calls = [];
  const apply = {disabled: false};
  const status = {className: "", textContent: ""};
  let sections = ["configuration"];
  const panel = {
    _backupTransferSession: "upload-1",
    _selectedAgent: () => ({entry_id: "entry-1", subentry_id: "agent-1"}),
    _hass: {callWS(message) {
      if (message.action === "import_cancel") return Promise.resolve({cancelled: true});
      assert.equal(message.action, "import_inspect");
      const response = deferred();
      calls.push({message, ...response});
      return response.promise;
    }},
    shadowRoot: {
      querySelector(selector) {
        if (selector === "#restore-transfer-apply") return apply;
        if (selector === "#restore-transfer-status") return status;
        return null;
      },
      querySelectorAll(selector) {
        assert.equal(selector, ".transfer-restore-section");
        return sections.map((value) => ({checked: true, value}));
      },
    },
  };
  return {panel, calls, apply, status, select(next) { sections = next; refreshImportPreview(panel); }};
}

test("A in flight, then B and C: only C follows A and only C can enable Restore", async () => {
  const h = previewHarness();
  h.select(["configuration"]);
  assert.equal(h.apply.disabled, true);
  await waitFor(() => h.calls.length === 1);
  h.select(["request_rules"]);
  h.select(["knowledge"]);
  await delay(180);
  assert.equal(h.calls.length, 1, "the latest selection must wait for the in-flight request");
  h.calls[0].resolve({preview: {selected_sections: ["configuration"]}});
  await waitFor(() => h.calls.length === 2);
  assert.deepEqual(h.calls.map((call) => call.message.data.sections), [["configuration"], ["knowledge"]]);
  assert.equal(h.apply.disabled, true);
  assert.equal(h.panel._backupTransferPreview, null);
  h.calls[1].resolve({preview: {selected_sections: ["knowledge"]}});
  await waitFor(() => h.apply.disabled === false);
  assert.deepEqual(h.panel._backupTransferPreview.selected_sections, ["knowledge"]);
});

test("clearing every section invalidates an in-flight preview immediately", async () => {
  const h = previewHarness();
  h.select(["configuration"]);
  await waitFor(() => h.calls.length === 1);
  h.select([]);
  assert.equal(h.apply.disabled, true);
  assert.match(h.status.textContent, /Select at least one section/);
  h.calls[0].resolve({preview: {selected_sections: ["configuration"]}});
  await delay(180);
  assert.equal(h.calls.length, 1);
  assert.equal(h.apply.disabled, true);
  assert.equal(h.panel._backupTransferPreview, null);
  assert.match(h.status.textContent, /Select at least one section/);
});

test("cancel clears a debounced preview and an in-flight result", async () => {
  const pending = previewHarness();
  pending.select(["configuration"]);
  await cancelBackupImport(pending.panel);
  await delay(180);
  assert.equal(pending.calls.length, 0);
  assert.equal(pending.panel._backupTransferSession, null);
  assert.equal(pending.apply.disabled, true);

  const inFlight = previewHarness();
  inFlight.select(["configuration"]);
  await waitFor(() => inFlight.calls.length === 1);
  await cancelBackupImport(inFlight.panel);
  inFlight.calls[0].resolve({preview: {selected_sections: ["configuration"]}});
  await delay(0);
  assert.equal(inFlight.apply.disabled, true);
  assert.equal(inFlight.panel._backupTransferPreview, null);
});

test("choosing a replacement file clears a pending preview before opening the picker", async () => {
  const h = previewHarness();
  h.select(["configuration"]);
  let opened = false;
  const input = {value: "old", click() { opened = true; }};
  openBackupPicker(h.panel, input);
  assert.equal(opened, true);
  assert.equal(input.value, "");
  assert.equal(h.apply.disabled, true);
  await delay(180);
  assert.equal(h.calls.length, 0);
  assert.equal(h.panel._backupTransferSession, null);
});

test("a preview error does not block a newer valid selection", async () => {
  const h = previewHarness();
  h.select(["configuration"]);
  await waitFor(() => h.calls.length === 1);
  h.calls[0].reject(new Error("Invalid combination"));
  await waitFor(() => h.status.textContent === "Invalid combination");
  assert.equal(h.apply.disabled, true);
  h.select(["request_rules"]);
  assert.equal(h.apply.disabled, true);
  await waitFor(() => h.calls.length === 2);
  h.calls[1].resolve({preview: {selected_sections: ["request_rules"]}});
  await waitFor(() => h.apply.disabled === false);
  assert.deepEqual(h.panel._backupTransferPreview.selected_sections, ["request_rules"]);
});

test("session replacement clears old work and accepts only the new session preview", async () => {
  const h = previewHarness();
  h.select(["configuration"]);
  await waitFor(() => h.calls.length === 1);
  await cancelBackupImport(h.panel);
  h.panel._backupTransferSession = "upload-2";
  h.select(["knowledge"]);
  h.calls[0].resolve({preview: {selected_sections: ["configuration"]}});
  await waitFor(() => h.calls.length === 2);
  assert.equal(h.calls[1].message.data.session_id, "upload-2");
  assert.equal(h.apply.disabled, true);
  h.calls[1].resolve({preview: {selected_sections: ["knowledge"]}});
  await waitFor(() => h.apply.disabled === false);
});
