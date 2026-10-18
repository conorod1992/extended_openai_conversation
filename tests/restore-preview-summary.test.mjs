import assert from 'node:assert/strict';
import {bindBackupTransfer, refreshImportPreview, restorePreviewSummaryLines} from '../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js';
import {backupSummaryLines} from '../custom_components/extended_openai_conversation_responses/frontend/backup-summary.js';
import {deferred} from './frontend-test-helpers.mjs';

const element = () => ({handlers: {}, disabled: false, innerHTML: '', textContent: '',
  addEventListener(type, fn) { this.handlers[type] = fn; }, querySelectorAll() { return []; },
  showModal() {}, close() {}});
const nodes = new Map(['#create-backup-transfer', '#restore-backup-transfer', '#backup-file-transfer',
  '#restore-transfer-apply', '#restore-transfer-cancel', '#restore-backup-name', '#restore-backup-meta',
  '#restore-summary', '#restore-transfer-sections', '#restore-transfer-status', '#restore-dialog'].map(id => [id, element()]));
const section = {checked: true, value: 'persistent_memory', addEventListener() {}};
nodes.get('#restore-transfer-sections').querySelectorAll = () => [section];
const actualSummary = {persistent_memories: 1, selected_sections: ['persistent_memory']};
let validationFails = false;
const headerSummary = {persistent_memories: 0};
const panel = {_selectedAgent: () => ({entry_id:'entry', subentry_id:'agent'}), _e: String,
  _setSaving() {}, _toast(message) { throw new Error(message); },
  shadowRoot: {querySelector: selector => nodes.get(selector) || null, querySelectorAll: () => [section]},
  _hass: {async callWS(message) {
    if (message.action === 'import_start') return {session_id:'upload', chunk_size:1, chunk_count:1};
    if (message.action === 'import_chunk') return {session_id:'upload', next_index:1, received:1};
    if (message.action === 'import_inspect' && message.data.inspect_only) return {
      valid:true, title:'Agent', source_kind:'custom_backup', available_sections:['persistent_memory'], summary:headerSummary, preview_token:null};
    if (message.action === 'import_inspect') {
      if (validationFails) throw new Error('Selected section failed validation');
      return {preview:actualSummary, preview_token:'valid-token'};
    }
    throw new Error(`Unexpected action ${message.action}`);
  }}
};
bindBackupTransfer(panel, backupSummaryLines);
const file = new Blob(['x']); Object.defineProperty(file, 'name', {value:'backup.json'});
await nodes.get('#backup-file-transfer').handlers.change({target:{files:[file]}});
assert.equal(nodes.get('#restore-summary').innerHTML, '', 'header-only placeholder counts must stay hidden');
assert.equal(nodes.get('#restore-transfer-apply').disabled, true);

// A slower earlier file must not replace the session selected after it.
const oldInspect = deferred();
const raceCalls = [];
const raceSection = {checked:true, value:'persistent_memory', addEventListener(){}};
const raceNodes = new Map(['#create-backup-transfer', '#restore-backup-transfer', '#backup-file-transfer',
  '#restore-transfer-apply', '#restore-transfer-cancel', '#restore-backup-name', '#restore-backup-meta',
  '#restore-summary', '#restore-transfer-sections', '#restore-transfer-status', '#restore-dialog'].map(id => [id, {
    handlers:{}, disabled:false, innerHTML:'', textContent:'', addEventListener(type, fn) { this.handlers[type] = fn; },
    querySelectorAll() { return [raceSection]; }, showModal(){}, close(){},
  }]));
const racePanel = {_selectedAgent: () => ({entry_id:'entry', subentry_id:'agent'}), _e:String, _setSaving(){},
  _toast(message) { raceCalls.push(['toast', message]); },
  shadowRoot:{querySelector:selector => raceNodes.get(selector) || null, querySelectorAll:()=>[raceSection]},
  _hass:{async callWS(message) {
    const {action, data} = message; raceCalls.push([action, data]);
    if (action === 'import_start') return {session_id:data.filename, chunk_size:1, chunk_count:1};
    if (action === 'import_chunk') return {session_id:data.session_id, next_index:1, received:1};
    if (action === 'import_inspect' && data.inspect_only && data.session_id === 'A') return oldInspect.promise;
    if (action === 'import_inspect' && data.inspect_only) return {valid:true,title:'B',source_kind:'full_backup',available_sections:['persistent_memory'],preview:{selected_sections:['persistent_memory']},preview_token:'B-token'};
    if (action === 'import_inspect') return {preview:{selected_sections:['persistent_memory']},preview_token:`${data.session_id}-preview`};
    if (action === 'import_cancel') return {cancelled:true};
    throw new Error(`Unexpected ${action}`);
  }}};
bindBackupTransfer(racePanel);
const fileNamed = name => { const file = new Blob(['x']); Object.defineProperty(file, 'name', {value:name}); return file; };
const uploadA = raceNodes.get('#backup-file-transfer').handlers.change({target:{files:[fileNamed('A')]}});
await new Promise(resolve => setTimeout(resolve, 0));
await raceNodes.get('#backup-file-transfer').handlers.change({target:{files:[fileNamed('B')]}});
assert.ok(raceCalls.some(([action, data]) => action === 'import_cancel' && data.session_id === 'A'),
  'superseding a fully-uploaded file cancels its still-active inspection session immediately');
await new Promise(resolve => setTimeout(resolve, 180));
assert.equal(racePanel._backupTransferPreviewToken, 'B-preview');
assert.equal(raceNodes.get('#restore-transfer-apply').disabled, false);
oldInspect.resolve({valid:true,title:'A',source_kind:'full_backup',available_sections:['persistent_memory'],preview:{selected_sections:['persistent_memory']},preview_token:'A-token'});
await uploadA;
assert.equal(racePanel._backupTransferSession, 'B');
assert.equal(racePanel._backupTransferInspection.title, 'B');
assert.equal(racePanel._backupTransferPreviewToken, 'B-preview', 'canceling stale A must preserve B\'s prepared preview token');
assert.equal(raceNodes.get('#restore-summary').innerHTML, '<li>0 persistent memories</li>', 'stale cleanup must not clear B\'s preview summary');
assert.equal(raceNodes.get('#restore-transfer-apply').disabled, false, 'stale cleanup must leave B restorable');
assert.ok(raceCalls.some(([action, data]) => action === 'import_cancel' && data.session_id === 'A'));

// A full preview that has not settled must not hold the panel-wide dispatch slot
// when a different backup is selected. Its eventual completion must remain stale.
const abandonedFullPreview = deferred();
const originalRaceWS = racePanel._hass.callWS;
let fullPreviewStarted = false;
racePanel._hass.callWS = async message => {
  if (message.action === 'import_inspect' && !message.data.inspect_only && message.data.session_id === 'B') {
    fullPreviewStarted = true;
    return abandonedFullPreview.promise;
  }
  return originalRaceWS(message);
};
refreshImportPreview(racePanel);
await new Promise(resolve => setTimeout(resolve, 180));
assert.equal(fullPreviewStarted, true);
await raceNodes.get('#backup-file-transfer').handlers.change({target:{files:[fileNamed('C')]}});
await new Promise(resolve => setTimeout(resolve, 180));
assert.equal(racePanel._backupTransferSession, 'C');
assert.equal(racePanel._backupTransferPreviewToken, 'C-preview', 'C validates while the superseded full preview is still unresolved');
assert.equal(raceNodes.get('#restore-transfer-apply').disabled, false);
assert.ok(raceCalls.some(([action, data]) => action === 'import_cancel' && data.session_id === 'B'));
abandonedFullPreview.reject(new Error('Superseded inspection cancelled'));
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(racePanel._backupTransferPreviewToken, 'C-preview');
assert.equal(raceNodes.get('#restore-transfer-apply').disabled, false);
await new Promise(resolve => setTimeout(resolve, 200));
assert.equal(panel._backupTransferPreviewToken, 'valid-token');
assert.equal(panel._backupTransferPreview.persistent_memories, 1);
assert.match(nodes.get('#restore-summary').innerHTML, /1 persistent memories/);
assert.equal(nodes.get('#restore-transfer-apply').disabled, false);
assert.equal(nodes.get('#restore-summary').innerHTML, '<li>1 persistent memories</li>');
assert.deepEqual(restorePreviewSummaryLines({selected_sections:['configuration','knowledge'], knowledge_sources:2, persistent_memories:99}),
  ['Agent configuration and Function Tools','2 Knowledge sources']);

section.value = 'knowledge';
validationFails = true;
refreshImportPreview(panel);
assert.equal(nodes.get('#restore-summary').innerHTML, '', 'selection changes invalidate counts immediately');
assert.equal(nodes.get('#restore-transfer-apply').disabled, true);
await new Promise(resolve => setTimeout(resolve, 200));
assert.equal(nodes.get('#restore-summary').innerHTML, '', 'failed validation cannot retain old counts');
assert.match(nodes.get('#restore-transfer-status').textContent, /failed validation/);
assert.equal(panel._backupTransferPreviewToken, null);

validationFails = false;
actualSummary.selected_sections = ['knowledge'];
actualSummary.knowledge_sources = 2;
refreshImportPreview(panel);
await new Promise(resolve => setTimeout(resolve, 200));
assert.equal(nodes.get('#restore-summary').innerHTML, '<li>2 Knowledge sources</li>');
assert.equal(nodes.get('#restore-transfer-apply').disabled, false);
section.checked = false;
refreshImportPreview(panel);
assert.equal(nodes.get('#restore-summary').innerHTML, '');
assert.equal(nodes.get('#restore-transfer-apply').disabled, true);
