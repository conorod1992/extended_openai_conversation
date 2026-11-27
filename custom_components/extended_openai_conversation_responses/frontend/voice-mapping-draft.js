import {UnsavedState, clone, same} from "./unsaved-state.js";
import {reviewRecoveredScope, scopeNeedsRecoveryReview, clearScopeRecoveryReview} from "./management-dialogs.js";

const states = new WeakMap();
const scopeRegistrations = new WeakMap();
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

function clearRecovery(state) {
  state.deferredBaseline = null;
  clearScopeRecoveryReview(scopeRegistrations.get(state));
}

function register(panel, state) {
  const scopes = panel._unsavedState ||= new UnsavedState();
  // Rendering the same rows must not replace the scope owning an open review.
  const scope = scopeRegistrations.get(state) || {
    isCurrent:() => current(panel, state),
    get baseline() { return state.baseline; },
    dirty:() => current(panel, state) && dirty(state),
    read:() => clone(projection(state.rows)),
    owns:destination => Boolean(destination) && current(panel, state),
    destinations:() => ["assistant/voice"],
    discard:() => {
      if (!current(panel, state)) return;
      // A detached source may already have transferred its serialized draft.
      // Retire those consumed values before row cleanup recomputes dirty state.
      if (panel.isConnected === false && panel._configDirty === false) panel._unsavedState?.scopes.get("configuration")?.discard?.();
      state.rows = fromMapping(baselineMapping(panel));
      state.baseline = projection(state.rows);
      clearRecovery(state);
      if (panel._draft) panel._draft.voice_device_mappings = clone(baselineMapping(panel));
      panel._syncConfigDirty?.();
    },
    restore:(target, retry) => { if (transferVoiceMappingDraft(target, panel, retry)) { target._syncConfigDirty?.(); target._render?.(); } },
  };
  scopeRegistrations.set(state, scope);
  scopes.register(`voice-mapping-rows:${key(state.owner)}`, scope);
}

export function transferVoiceMappingDraft(target, source, retry) {
  const state = existing(source);
  if (!state || !current(source, state) || !dirty(state) || source.isConnected !== false
      || target.isConnected === false || !target._data?.is_admin
      || !target._draft || target._draftAgentId !== target._agentId) return false;
  const receivingOwner = owner(target);
  if (["agent", "entry", "user"].some(field => receivingOwner[field] !== state.owner[field])) return false;
  const prior = existing(target);
  const competing = prior && dirty(prior);
  if (competing) state.deferredBaseline ||= {revision:target._configData?.revision};
  if (competing || scopeNeedsRecoveryReview(scopeRegistrations.get(state))
      || state.deferredBaseline && state.deferredBaseline.revision !== target._configData?.revision) {
    // A later successful save is not consent to receive the deferred rows.
    // Retain both copies, including across replacement, until explicit review.
    if (target._viewKey?.() === "assistant/voice") {
      voiceMappingDraft(target);
      reviewRecoveredScope(target, source, `voice-mapping-rows:${key(state.owner)}`,
        "Voice assignments", retry, {targetId:`voice-mapping-rows:${key(owner(target))}`, requireReview:true});
    }
    return false;
  }
  const targetMapping = mapping(target._draft.voice_device_mappings);
  const sourceMapping = validateRows(state.rows, state).mapping || mapping(source._draft?.voice_device_mappings);
  if (!same(targetMapping, baselineMapping(target)) && !same(targetMapping, sourceMapping)) return false;
  const receiving = voiceMappingDraft(target);
  // Keep an off-route draft's known conflicts until the receiving panel can
  // verify the registry. A lookup already made by that panel takes precedence.
  if (receiving.registryOrder === undefined) {
    for (const [id, {entry}] of state.aliases) {
      if (!receiving.aliases.has(id)) receiving.aliases.set(id, {entry});
    }
  }
  // Equal projections can keep their mounted widgets. Preserve their row IDs
  // so those widgets still address the receiving state after recovery.
  if (!same(projection(receiving.rows), projection(state.rows))) receiving.rows = clone(state.rows);
  receiving.baseline = projection(fromMapping(baselineMapping(target)));
  // A deferred row-only recovery can outlive the original configuration handoff.
  // Keep the valid persistence map aligned with the rows the editor now owns.
  const resolved = validateRows(receiving.rows, receiving).mapping;
  if (resolved) target._draft.voice_device_mappings = resolved;
  state.rows = fromMapping(baselineMapping(source));
  state.baseline = projection(state.rows);
  clearRecovery(state);
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
    state = {owner:owner(panel), rows, baseline:projection(fromMapping(baselineMapping(panel))), aliases:new Map()};
    collection.set(key(state.owner), state);
  }
  register(panel, state);
  return state;
}

export function newVoiceMappingRow() {
  return {id:`voice-row-${++rowSequence}`, deviceId:"", owner:"unretained", satellite:"", lookup:""};
}

export function rememberVoiceDeviceAliases(panel, entries, {listing=false, order=0} = {}) {
  const state = voiceMappingDraft(panel);
  const {aliases} = state;
  const lastListing = state.registryOrder ?? 0;
  if (entries && listing && order >= lastListing) {
    state.registryOrder = order;
    // A full listing replaces earlier evidence, including associations it omits.
    for (const [id, latest] of aliases) {
      if ((latest.order || 0) <= order) aliases.delete(id);
    }
  }
  if (order >= lastListing) {
    for (const entry of entries || []) {
      const id = entry?.entity_id;
      if (id?.startsWith("assist_satellite.") && (aliases.get(id)?.order || 0) <= order) {
        aliases.set(id, {entry, order});
      }
    }
  }
  return Array.from(aliases.values(), alias => alias.entry);
}

export function voiceMappingDeviceKey(state, id) {
  return state?.aliases.get(id)?.entry.device_id || id;
}

function validateRows(rows, state) {
  const result = Object.create(null);
  const devices = new Set();
  for (const row of rows) {
    if (row.lookup === "pending") return {error:"Wait for the Assist satellite device lookup to finish.", mapping:null};
    if (row.lookup) return {error:"Select an available Assist satellite before saving.", mapping:null};
    if (!row.deviceId) return {error:"Choose an Assist satellite or remove the empty assignment before saving.", mapping:null};
    const device = voiceMappingDeviceKey(state, row.deviceId);
    if (devices.has(device)) return {error:"This Assist satellite is assigned more than once. Choose an assignment to keep or remove the extra assignment.", mapping:null};
    devices.add(device);
    result[row.deviceId] = row.owner ?? "unretained";
  }
  const savedIds = new Set(state?.baseline.map(row => row.deviceId));
  if (state?.baseline.some(row => {
    const device = voiceMappingDeviceKey(state, row.deviceId);
    return device !== row.deviceId && Object.hasOwn(result, row.deviceId)
      && !Object.hasOwn(result, device) && savedIds.has(device);
  })) {
    return {error:"Removing the device assignment would reactivate its older satellite assignment. Keep the older assignment explicitly or remove it too.", mapping:null};
  }
  if (state && state.registryOrder === undefined && state.baseline.some(saved => !saved.deviceId.startsWith("assist_satellite.")
      && !Object.hasOwn(result, saved.deviceId))
      && rows.some(row => row.deviceId.startsWith("assist_satellite.")
        && !state.aliases.has(row.deviceId))) {
    return {error:"Verify the older satellite assignments before removing a device assignment, or remove the older assignments too.", mapping:null};
  }
  return {error:"", mapping:{...result}};
}

export function updateVoiceMappingRows(panel, rows) {
  const state = voiceMappingDraft(panel);
  state.rows = clone(rows);
  const validated = validateRows(state.rows, state);
  // The persistence draft always contains a map. Invalid authoring rows and
  // diagnostics have their own lifetime and never masquerade as field values.
  if (validated.mapping && panel._draft) panel._draft.voice_device_mappings = validated.mapping;
  return validated;
}

export function voiceMappingDraftError(panel) {
  const state = existing(panel);
  return state ? validateRows(state.rows, state).error : "";
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
  clearRecovery(state);
}

export function acknowledgeVoiceMappingDraft(panel, saved, submission, fields) {
  const written = fields ?? submission.fields;
  if ((written && !written.includes("voice_device_mappings")) || !Object.hasOwn(saved.config || {}, "voice_device_mappings")) return;
  const state = existing(panel);
  if (!state) return;
  const savedRows = fromMapping(saved.config.voice_device_mappings);
  if (same(projection(state.rows), submission.voiceMappingRows)) state.rows = savedRows.map(row => {
    const retained = state.rows.find(item => item.deviceId === row.deviceId);
    return retained ? {...row,id:retained.id,satellite:retained.satellite} : row;
  });
  state.baseline = projection(savedRows);
  clearRecovery(state);
}
