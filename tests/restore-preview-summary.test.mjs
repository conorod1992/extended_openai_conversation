import assert from 'node:assert/strict';
import {bindBackupTransfer} from '../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js';
import {backupSummaryLines} from '../custom_components/extended_openai_conversation_responses/frontend/backup-summary.js';

const element = () => ({handlers: {}, disabled: false, innerHTML: '', textContent: '',
  addEventListener(type, fn) { this.handlers[type] = fn; }, querySelectorAll() { return []; },
  showModal() {}, close() {}});
const nodes = new Map(['#create-backup-transfer', '#restore-backup-transfer', '#backup-file-transfer',
  '#restore-transfer-apply', '#restore-transfer-cancel', '#restore-backup-name', '#restore-backup-meta',
  '#restore-summary', '#restore-transfer-sections', '#restore-transfer-status', '#restore-dialog'].map(id => [id, element()]));
const section = {checked: true, value: 'persistent_memory', addEventListener() {}};
nodes.get('#restore-transfer-sections').querySelectorAll = () => [section];
const actualSummary = {persistent_memories: 1, selected_sections: ['persistent_memory']};
const headerSummary = {persistent_memories: 0};
const panel = {_selectedAgent: () => ({entry_id:'entry', subentry_id:'agent'}), _e: String,
  _setSaving() {}, _toast(message) { throw new Error(message); },
  shadowRoot: {querySelector: selector => nodes.get(selector) || null, querySelectorAll: () => [section]},
  _hass: {async callWS(message) {
    if (message.action === 'import_start') return {session_id:'upload', chunk_size:1, chunk_count:1};
    if (message.action === 'import_chunk') return {session_id:'upload', next_index:1, received:1};
    if (message.action === 'import_inspect' && message.data.inspect_only) return {
      valid:true, title:'Agent', source_kind:'custom_backup', available_sections:['persistent_memory'], summary:headerSummary, preview_token:null};
    if (message.action === 'import_inspect') return {preview:actualSummary, preview_token:'valid-token'};
    throw new Error(`Unexpected action ${message.action}`);
  }}
};
bindBackupTransfer(panel, backupSummaryLines);
const file = new Blob(['x']); Object.defineProperty(file, 'name', {value:'backup.json'});
await nodes.get('#backup-file-transfer').handlers.change({target:{files:[file]}});
assert.equal(nodes.get('#restore-summary').innerHTML, '', 'header-only placeholder counts must stay hidden');
assert.equal(nodes.get('#restore-transfer-apply').disabled, true);
await new Promise(resolve => setTimeout(resolve, 200));
assert.equal(panel._backupTransferPreviewToken, 'valid-token');
assert.equal(panel._backupTransferPreview.persistent_memories, 1);
assert.match(nodes.get('#restore-summary').innerHTML, /1 persistent memories/);
assert.equal(nodes.get('#restore-transfer-apply').disabled, false);
