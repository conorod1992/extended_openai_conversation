import assert from "node:assert/strict";

import {
  decorateRestoreDialog,
  downloadCustomBackup,
  downloadSetupExport,
  TRANSFER_SECTIONS,
  WS_BACKUP_TRANSFER,
} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";

const makePanel = (handler) => ({
  _selectedAgent: () => ({entry_id: "entry-1", subentry_id: "agent-1"}),
  _hass: {callWS: handler},
});

{
  const calls = [];
  let saved = null;
  const panel = makePanel(async (message) => {
    calls.push(message);
    assert.equal(message.type, WS_BACKUP_TRANSFER);
    assert.equal(message.action, "setup_export");
    return {
      json: '{"format":"extended_openai_conversation_transfer"}',
      filename: "jarvis-shareable-setup-2026-09-08.json",
      mode: "setup",
    };
  });

  const result = await downloadSetupExport(panel, async (blob, filename) => {
    saved = {text: await blob.text(), filename, type: blob.type};
  });

  assert.equal(result.mode, "setup");
  assert.equal(saved.filename, "jarvis-shareable-setup-2026-09-08.json");
  assert.equal(saved.type, "application/json");
  assert.match(saved.text, /extended_openai_conversation_transfer/);
  assert.equal(calls.length, 1);
}

{
  const selected = ["configuration", "request_rules", "knowledge"];
  const calls = [];
  let saved = null;
  const panel = makePanel(async (message) => {
    calls.push(message);
    if (message.action === "export_start") {
      assert.equal(message.data.mode, "custom");
      assert.deepEqual(message.data.sections, selected);
      return {
        session_id: "custom-1",
        mode: "custom",
        size: 3,
        chunk_count: 1,
        content_type: "application/zip",
        filename: "jarvis-custom-backup-2026-09-08.zip",
      };
    }
    if (message.action === "export_chunk") {
      return {
        session_id: "custom-1",
        index: 0,
        offset: 0,
        bytes: 3,
        data: globalThis.btoa(String.fromCharCode(1, 2, 3)),
      };
    }
    if (message.action === "export_cancel") return {cancelled: true};
    throw new Error(`Unexpected action ${message.action}`);
  });

  await downloadCustomBackup(panel, selected, async (blob, filename) => {
    saved = {bytes: [...new Uint8Array(await blob.arrayBuffer())], filename};
  });

  assert.deepEqual(saved.bytes, [1, 2, 3]);
  assert.equal(saved.filename, "jarvis-custom-backup-2026-09-08.zip");
  assert.deepEqual(calls.map((item) => item.action), ["export_start", "export_chunk", "export_cancel"]);
}

await assert.rejects(
  () => downloadCustomBackup(makePanel(async () => ({})), [], async () => {}),
  /Select at least one section/,
);

{
  const dialog = decorateRestoreDialog("unsaved configuration changes");
  assert.match(dialog, /Import \/ Restore/);
  assert.match(dialog, /Replacement, not merge/);
  assert.match(dialog, /Sections to replace/);
  assert.match(dialog, /unsaved configuration changes/i);
  assert.match(dialog, /restore-transfer-sections/);
}

assert.deepEqual(
  TRANSFER_SECTIONS.map(([key]) => key),
  [
    "configuration",
    "request_rules",
    "persistent_memory",
    "temporary_memory",
    "knowledge",
    "conversation_archive",
    "usage",
    "guest_mode",
  ],
);
