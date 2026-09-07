import assert from "node:assert/strict";

import {
  base64ToBytes,
  bytesToBase64,
  decorateBackupMarkup,
  decorateRestoreDialog,
  downloadFullBackup,
  uploadFullBackup,
  WS_BACKUP_TRANSFER,
} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";

const makePanel = (handler) => ({
  _selectedAgent: () => ({entry_id: "entry-1", subentry_id: "agent-1"}),
  _hass: {callWS: handler},
});

assert.deepEqual([...base64ToBytes(bytesToBase64(new Uint8Array([0, 1, 127, 128, 255])))], [0, 1, 127, 128, 255]);

{
  const source = new Uint8Array([1, 2, 3, 4, 5]);
  const calls = [];
  let saved = null;
  const panel = makePanel(async (message) => {
    calls.push(message);
    assert.equal(message.type, WS_BACKUP_TRANSFER);
    if (message.action === "export_start") {
      return {session_id: "export-1", size: source.length, chunk_count: 2, content_type: "application/zip", filename: "agent.zip"};
    }
    if (message.action === "export_chunk") {
      const start = message.data.index === 0 ? 0 : 3;
      const bytes = source.slice(start, message.data.index === 0 ? 3 : 5);
      return {
        session_id: "export-1",
        index: message.data.index,
        offset: start,
        bytes: bytes.length,
        data: bytesToBase64(bytes),
      };
    }
    if (message.action === "export_cancel") return {cancelled: true};
    throw new Error(`Unexpected action ${message.action}`);
  });

  const manifest = await downloadFullBackup(panel, async (blob, filename) => {
    saved = {bytes: new Uint8Array(await blob.arrayBuffer()), filename};
  });

  assert.equal(manifest.filename, "agent.zip");
  assert.deepEqual([...saved.bytes], [...source]);
  assert.equal(saved.filename, "agent.zip");
  assert.deepEqual(calls.map((item) => item.action), ["export_start", "export_chunk", "export_chunk", "export_cancel"]);
}

{
  const calls = [];
  const panel = makePanel(async (message) => {
    calls.push(message.action);
    if (message.action === "export_start") return {session_id: "bad", size: 4, chunk_count: 1};
    if (message.action === "export_chunk") return {session_id: "bad", index: 0, offset: 1, bytes: 4, data: bytesToBase64(new Uint8Array([1, 2, 3, 4]))};
    if (message.action === "export_cancel") return {cancelled: true};
    throw new Error("unexpected action");
  });

  await assert.rejects(() => downloadFullBackup(panel, async () => {}), /out-of-order/);
  assert.deepEqual(calls, ["export_start", "export_chunk", "export_cancel"]);
}

{
  const source = new Uint8Array([9, 8, 7, 6, 5]);
  const file = new Blob([source], {type: "application/json"});
  Object.defineProperty(file, "name", {value: "legacy.json"});
  const received = [];
  const calls = [];
  const panel = makePanel(async (message) => {
    calls.push(message.action);
    if (message.action === "import_start") {
      assert.equal(message.data.filename, "legacy.json");
      assert.equal(message.data.size, source.length);
      return {session_id: "import-1", chunk_size: 2, chunk_count: 3};
    }
    if (message.action === "import_chunk") {
      const bytes = base64ToBytes(message.data.data);
      received.push(...bytes);
      return {
        session_id: "import-1",
        next_index: message.data.index + 1,
        received: received.length,
      };
    }
    if (message.action === "import_inspect") {
      return {
        valid: true,
        title: "Agent",
        summary: {created_at: "2026-09-07T00:00:00+00:00", integration_version: "4.6.0"},
      };
    }
    throw new Error(`Unexpected action ${message.action}`);
  });

  const result = await uploadFullBackup(panel, file);
  assert.equal(result.session_id, "import-1");
  assert.equal(result.title, "Agent");
  assert.deepEqual(received, [...source]);
  assert.deepEqual(calls, ["import_start", "import_chunk", "import_chunk", "import_chunk", "import_inspect"]);
}

{
  const markup = decorateBackupMarkup('<button id="create-backup"></button><button id="restore-backup"></button><input id="backup-file" type="file" accept="application/json,.json" hidden>');
  assert.doesNotMatch(markup, /id="create-backup"/);
  assert.doesNotMatch(markup, /id="restore-backup"/);
  assert.match(markup, /id="create-backup-transfer"/);
  assert.match(markup, /id="restore-backup-transfer"/);
  assert.match(markup, /application\/zip/);

  const dialog = decorateRestoreDialog('<button id="restore-cancel"></button><button id="restore-apply"></button>');
  assert.match(dialog, /restore-transfer-cancel/);
  assert.match(dialog, /restore-transfer-apply/);
  assert.doesNotMatch(dialog, /id="restore-apply"/);
}
