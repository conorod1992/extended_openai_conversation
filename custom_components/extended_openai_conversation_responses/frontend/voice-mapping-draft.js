import {UnsavedState, clone, same} from "./unsaved-state.js";

const states = new WeakMap();
let rowSequence = 0;
const owner = panel => ({agent:panel._agentId, entry:panel._selectedAgent?.()?.entry_id,
  user:panel.hass?.user?.id, generation:panel._eocDraftGeneration || 0});
const key = value => JSON.stringify(value);
const mapping = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
const baselineMapping = panel => mapping((panel._eocAcknowledgedConfiguration?.baseline?.() || panel._configData || panel._result)?.config?.voice_device_mappings);
const projection = rows => rows.map(row => ({deviceId:row.deviceId, owner:row.owner,
  ...(row.lookup ? {satellite:row.satellite, lookup:row.lookup} : {})}));
const fromMapping = value => Object.entries(mapping(value)).map(([deviceId, retainedOwner]) =>
  ({id:`voice-row-${++rowSequence}`, deviceId, owner:retainedOwner, satellite:"", lookup:""}));
const current = (panel, state) => key(owner(panel)) === key(state.owner);

function existing(panel) {
  const collection = states.get(panel);
  const identity = owner(panel);
  let state = collection?.get(key(identity));
  if (!state && identity.user != null) {
    const initial = {...identity, user:undefined};
    state = collection?.get(key(initial));
    if (state) {
      collection.delete(key(initial));
      panel._unsavedState?.scopes?.delete(`voice-mapping-rows:${key(initial)}`);
      state.owner = identity;
      collection.set(key(identity), state);
    }
  }
  return state;
}

function dirty(state) {
  return !same(projection(state.rows), state.baseline) || Boolean(validateRows(state.rows).error);
}

function register(panel, state) {
  const scopes = panel._unsavedState ||= new UnsavedState();
  scopes.register(`voice-mapping-rows:${key(state.owner)}`, {
    dirty:() => current(panel, state) && dirty(state),
    read:() => clone(state.rows),
    owns:destination => Boolean(destination) && current(panel, state),
    destinations:() => ["assistant/voice"],
    discard:() => {
      if (!current(panel, state)) return;
      state.rows = fromMapping(baselineMapping(panel));
      state.baseline = projection(state.rows);
      if (panel._draft) panel._draft.voice_device_mappings = clone(baselineMapping(panel));
      panel._syncConfigDirty?.();
    },
    restore:target => { if (transferVoiceMappingDraft(target, panel)) { target._syncConfigDirty?.(); target._render?.(); } },
  });
}

export function transferVoiceMappingDraft(target, source) {
  const state = existing(source);
  if (!state || !current(source, state) || !dirty(state) || source.isConnected !== false
      || target.isConnected === false || !target._data?.is_admin
      || !target._draft || target._draftAgentId !== target._agentId) return false;
  const receivingOwner = owner(target);
  if (["agent", "entry", "user"].some(field => receivingOwner[field] !== state.owner[field])) return false;
  const prior = existing(target);
  if (prior && dirty(prior)) return false;
  const validated = validateRows(state.rows);
  const targetMapping = mapping(target._draft.voice_device_mappings);
  const sourceMapping = validated.mapping || mapping(source._draft?.voice_device_mappings);
  if (!same(targetMapping, baselineMapping(target)) && !same(targetMapping, sourceMapping)) return false;
  const receiving = voiceMappingDraft(target);
  receiving.rows = clone(state.rows);
  receiving.baseline = clone(state.baseline);
  // A deferred row-only recovery can outlive the original configuration handoff.
  // Keep the valid persistence map aligned with the rows the editor now owns.
  if (validated.mapping) target._draft.voice_device_mappings = clone(validated.mapping);
  state.rows = fromMapping(baselineMapping(source));
  state.baseline = projection(state.rows);
  return true;
}

export function voiceMappingDraft(panel) {
  let state = existing(panel);
  if (state && !dirty(state) && !same(state.baseline, projection(fromMapping(baselineMapping(panel))))) {
    state.rows = fromMapping(panel._draft?.voice_device_mappings ?? baselineMapping(panel));
    state.baseline = projection(fromMapping(baselineMapping(panel)));
  }
  if (!state) {
    let collection = states.get(panel);
    if (!collection) states.set(panel, collection = new Map());
    const rows = fromMapping(panel._draft?.voice_device_mappings ?? baselineMapping(panel));
    state = {owner:owner(panel), rows, baseline:projection(fromMapping(baselineMapping(panel)))};
    collection.set(key(state.owner), state);
  }
  register(panel, state);
  return state;
}

export function newVoiceMappingRow() {
  return {id:`voice-row-${++rowSequence}`, deviceId:"", owner:"unretained", satellite:"", lookup:""};
}

function validateRows(rows) {
  const result = {};
  for (const row of rows) {
    if (row.lookup === "pending") return {error:"Wait for the Assist satellite device lookup to finish.", mapping:null};
    if (row.lookup) return {error:"Select an available Assist satellite before saving.", mapping:null};
    if (!row.deviceId) return {error:"Choose an Assist satellite or remove the empty assignment before saving.", mapping:null};
    if (Object.hasOwn(result, row.deviceId)) return {error:"This Assist satellite is assigned more than once.", mapping:null};
    result[row.deviceId] = row.owner || "unretained";
  }
  return {error:"", mapping:result};
}

export function updateVoiceMappingRows(panel, rows) {
  const state = voiceMappingDraft(panel);
  state.rows = clone(rows);
  const validated = validateRows(state.rows);
  // The persistence draft always contains a map. Invalid authoring rows and
  // diagnostics have their own lifetime and never masquerade as field values.
  if (validated.mapping && panel._draft) panel._draft.voice_device_mappings = validated.mapping;
  return validated;
}

export function voiceMappingDraftError(panel) {
  const state = existing(panel);
  return state ? validateRows(state.rows).error : "";
}

export function voiceMappingDraftDirty(panel) {
  const state = existing(panel);
  return Boolean(state && dirty(state));
}

export function captureVoiceMappingDraft(panel) {
  const state = existing(panel);
  return state ? projection(state.rows) : null;
}

export function voiceMappingRowIsCurrent(panel, rowId) {
  return Boolean(existing(panel)?.rows.some(row => row.id === rowId));
}

export function clearVoiceMappingDraft(panel) {
  states.delete(panel);
  for (const id of panel._unsavedState?.scopes?.keys() || []) {
    if (id.startsWith("voice-mapping-rows:")) panel._unsavedState.scopes.delete(id);
  }
}

export function discardVoiceMappingDraft(panel, savedMapping = baselineMapping(panel)) {
  const state = existing(panel);
  if (!state) return;
  state.rows = fromMapping(savedMapping);
  state.baseline = projection(state.rows);
}

export function acknowledgeVoiceMappingDraft(panel, saved, submission, fields) {
  if ((fields && !fields.includes("voice_device_mappings")) || !Object.hasOwn(saved.config || {}, "voice_device_mappings")) return;
  const state = existing(panel);
  if (!state) return;
  const savedRows = fromMapping(saved.config.voice_device_mappings);
  if (same(projection(state.rows), submission.voiceMappingRows)) state.rows = savedRows.map(row => {
    const retained = state.rows.find(item => item.deviceId === row.deviceId);
    return retained ? {...row,id:retained.id,satellite:retained.satellite} : row;
  });
  state.baseline = projection(savedRows);
}
