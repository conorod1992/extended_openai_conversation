import {clone, same, mergeAcknowledgedDraft} from "./unsaved-state.js";

import {callMutation, processMutationResponse, requireMutationAcknowledgement, mutationResponseError} from "./mutation-response.js";
export {callMutation, processMutationResponse, requireMutationAcknowledgement};

const field = (config, key) => Object.hasOwn(config, key) ? [config[key]] : [];

// Authored values survive a temporary identity transition. An operation made
// before it cannot regain authority merely because the same user returns.
export function captureConfigurationDraftOwner(panel) {
  return {agentId:panel._agentId, entryId:panel._selectedAgent?.()?.entry_id,
    userId:panel.hass?.user?.id, draftGeneration:panel._eocDraftGeneration || 0};
}

export function captureConfigurationOwner(panel) {
  return {...captureConfigurationDraftOwner(panel), authenticationGeneration:panel._eocAuthenticationGeneration || 0};
}

export function captureConfigurationSubmission(panel) {
  return {
    ...captureConfigurationOwner(panel),
    view:panel._viewKey?.(),
    revision:panel._configData?.revision,
    readEpoch:panel._eocConfigurationReadEpoch || 0,
    modelIntent:panel._eocModelIntent || null,
    reasoningChoiceRevision:panel._eocReasoningChoiceRevision || 0,
    modelDefaultsRevision:panel._eocModelIntent?.reasoningDefaultRevision || 0,
    baseline:clone(panel._configData?.config || {}),
    draft:clone(panel._draft || {}), title:panel._draftTitle,
  };
}

export function configurationOwnerIsCurrent(panel, submission) {
  return panel._agentId === submission.agentId
    && panel._selectedAgent?.()?.entry_id === submission.entryId
    && (!Object.hasOwn(submission, "userId") || panel.hass?.user?.id === submission.userId);
}

export function configurationSubmissionIsCurrent(panel, submission) {
  return configurationOwnerIsCurrent(panel, submission)
    // Identity-only collection refresh descriptors remain supported. Draft
    // authoring and queue admission capture the full owner above.
    && (!Object.hasOwn(submission, "draftGeneration") || (panel._eocDraftGeneration || 0) === submission.draftGeneration)
    && (!Object.hasOwn(submission, "authenticationGeneration") || (panel._eocAuthenticationGeneration || 0) === submission.authenticationGeneration);
}

export function configurationReadOvertookSubmission(panel, submission) {
  return (panel._eocConfigurationReadEpoch || 0) !== (submission.readEpoch || 0);
}

function preservesModelPreparation(panel, submission, fields) {
  const ownsModel = !fields || fields.some(key => ["chat_model", "azure_model", "reasoning_effort"].includes(key));
  return !ownsModel || (panel._eocModelIntent || null) !== (submission.modelIntent || null);
}

// The saved projection and the baseline of a pending edit have different
// lifetimes. Acknowledged fields describe this write; unrelated fields still
// belong to the latest accepted read.
export function configurationDraftBaseline(panel, baseline = panel._configData) {
  return panel._eocAcknowledgedConfiguration?.baseline?.(baseline) || baseline;
}

export function discardConfigurationDraft(panel) {
  if (panel._configurationSaving) return false;
  const baseline = configurationDraftBaseline(panel);
  if (!baseline?.config) return false;
  panel._draft = clone(baseline.config);
  panel._draftTitle = baseline.title;
  if (panel._eocAcknowledgedConfiguration) {
    // Discard the authored delta, not the completed write's refresh obligation.
    // An older read cannot certify the next write or become a recovered edit.
    panel._invalidateCleanConfiguration?.(panel._agentId);
    panel._configDataStale = true;
  }
  panel._setConfigDirty(false);
  return true;
}

export function mergeConfigurationRead(panel, current, baseline, saved) {
  const restored = panel._eocRestoredConfiguration;
  // Restore replaces the submitted draft. Only subsequently authored edits survive.
  const comparison = restored && configurationSubmissionIsCurrent(panel, restored)
    ? {config:restored.draft, title:restored.title} : configurationDraftBaseline(panel, baseline);
  const pending = panel._eocConfigurationSubmission;
  const protectedFields = pending && !pending.acknowledged && configurationSubmissionIsCurrent(panel, pending) ? pending.fields : [];
  panel._eocModelIntent?.acceptReasoningRead?.(panel, saved.config, {comparisonConfig:comparison.config, protectedFields});
  const next = mergeAcknowledgedDraft(current.draft, comparison.config, saved.config);
  let title = current.title !== comparison.title ? current.title : saved.title;
  if (pending && !pending.acknowledged && configurationSubmissionIsCurrent(panel, pending)) {
    // Reads made before the write commits cannot acknowledge its submitted
    // fields, nor erase a later reversion to the original saved value.
    for (const key of pending.fields) {
      if (Object.hasOwn(current.draft, key)) next[key] = clone(current.draft[key]);
      else delete next[key];
    }
    if (pending.writesTitle) title = current.title;
  }
  return {draft:next, title};
}

function acknowledgedFieldsBaseline(baseline, saved, submission) {
  const config = {...baseline.config};
  for (const key of submission.fields) {
    if (Object.hasOwn(saved.config, key)) config[key] = clone(saved.config[key]);
    else delete config[key];
  }
  return {...baseline, config, title:submission.writesTitle ? saved.title : baseline.title};
}

function retainConfigurationAcknowledgement(panel, saved, submission) {
  if (panel._eocConfigurationSubmission !== submission) return;
  submission.acknowledged = true;
  const comparison = acknowledgedFieldsBaseline(panel._configData || {config:submission.baseline, title:submission.title}, saved, submission);
  panel._eocModelIntent?.acknowledgeReasoning?.(panel, saved.config, submission, comparison.config);
  // Canonicalize only unchanged submitted fields, even if a newer read owns
  // the saved projection. Normalization is not a newly authored edit.
  if (panel._draft && panel._draftAgentId === submission.agentId) {
    const previous = panel._draft;
    const next = mergeAcknowledgedDraft(previous, submission.draft, saved.config, submission.fields);
    if (preservesModelPreparation(panel, submission)) panel._eocPendingModelPreparation?.rebindDraft?.(previous, next);
    panel._draft = next;
    if (submission.writesTitle && panel._draftTitle === submission.title) panel._draftTitle = saved.title;
  }
  const user = panel.hass?.user?.id;
  const owns = target => configurationOwnerIsCurrent(target, submission) && target.hass?.user?.id === user
    // A source must still own its submitted session. A replacement destination
    // has its own session and acquires the delta through explicit recovery.
    && (target !== panel || configurationSubmissionIsCurrent(panel, submission));
  const states = new WeakMap();
  const acknowledgement = (destination, retry) => {
    if (!owns(panel) || !owns(destination) || panel.isConnected !== false
        || destination.isConnected === false || !destination._data?.is_admin) return {accepted:false, refreshing:false};
    let state = states.get(destination);
    if (state?.running) return {accepted:false, refreshing:true};
    if (state && !destination._configDataStale && destination._configData?.config && destination._configData.projection !== "retention"
        && (destination._eocConfigurationReadEpoch || 0) > state.epoch) {
      // The source is detached and cannot dispatch another save. Preserve its
      // acknowledged authoring baseline until the lifecycle transfers its delta.
      // Destination edits are resolved by that same lifecycle's merge/conflict
      // planner, not by withholding this completed-write handoff indefinitely.
      panel._configData = acknowledgement.baseline();
      panel._syncConfigDirty?.();
      if (destination._result === destination._configData) destination._render?.();
      return {accepted:true, refreshing:false};
    }
    if (state?.failed && state.visit === destination._loadToken) return {accepted:false, refreshing:false};
    state = {running:true, epoch:destination._eocConfigurationReadEpoch || 0, visit:destination._loadToken};
    states.set(destination, state);
    if (destination._invalidateAfterMutation) destination._invalidateAfterMutation(submission.agentId, "configuration", "save");
    else destination._invalidateCleanConfiguration?.(submission.agentId);
    destination._configDataStale = true;
    void destination._loadConfigDraft({preserveResult:true, full:true}).catch(() => {
      state.failed = true;
      if (owns(destination) && destination.isConnected !== false) destination._toast?.("Changes saved, but configuration could not be refreshed. Refresh this section to retry.", true);
    }).finally(() => {
      state.running = false;
      // Retry through the normal access, pending-work and current-panel gates.
      retry?.();
    });
    return {accepted:false, refreshing:true};
  };
  acknowledgement.baseline = (baseline = panel._configData || {config:submission.baseline, title:submission.title}) => {
    if (!owns(panel)) return null;
    return acknowledgedFieldsBaseline(baseline, saved, submission);
  };
  panel._eocAcknowledgedConfiguration = acknowledgement;
}

// A full save replaces the saved projection. Independent saves acknowledge only
// their own fields, leaving unrelated persisted values and local edits alone.
export function reconcileConfigurationAcknowledgement(panel, saved, submission, {fields, metadata = []} = {}) {
  if (!configurationSubmissionIsCurrent(panel, submission) || saved?.valid === false || !saved?.config) return false;
  if (!fields) retainConfigurationAcknowledgement(panel, saved, submission);
  if (configurationReadOvertookSubmission(panel, submission)) {
    // A response describes the commit it acknowledges, not necessarily the
    // latest persisted configuration. Keep an accepted newer read until an
    // authoritative re-read can establish the current state after this write.
    panel._invalidateCleanConfiguration?.(submission.agentId);
    panel._configDataStale = true;
    if (panel._eocPendingModelPreparation && !preservesModelPreparation(panel, submission, fields)) {
      // A save that commits the active model intent also retires its defaults
      // when a later read, rather than this response, owns the baseline.
      panel._eocPendingModelPreparation = null;
      panel._eocModelChangeToken = (panel._eocModelChangeToken || 0) + 1;
    }
    panel._syncConfigDirty?.();
    return true;
  }
  if (fields && !panel._configData?.config) return true;
  const full = !fields;
  const keys = fields?.filter(key => Object.hasOwn(saved.config, key));
  const updates = full ? clone(saved.config)
    : Object.fromEntries(keys.map(key => [key, clone(saved.config[key])]));
  const {valid: _valid, errors: _errors, agent, ...snapshot} = saved;
  const previous = panel._configData;
  panel._configData = full ? {...panel._configData, ...snapshot, config:updates}
    : {...panel._configData, config:{...panel._configData.config, ...updates},
      ...(saved.revision !== undefined ? {revision:saved.revision} : {})};
  for (const key of full ? new Set(["function_repair", ...metadata]) : metadata) {
    if (Object.hasOwn(saved, key)) panel._configData[key] = clone(saved[key]);
    else delete panel._configData[key];
  }
  if (panel._result === previous) panel._result = panel._configData;
  if (full) {
    panel._configDataStale = false;
    panel._rememberCleanConfiguration?.(panel._configData);
  } else {
    // A field acknowledgement cannot certify the rest of the snapshot. Function
    // renames also update Guest allowlists which are absent from this response.
    panel._invalidateCleanConfiguration?.(submission.agentId);
    panel._configDataStale = true;
  }
  if (panel._draft && panel._draftAgentId === submission.agentId) {
    const submitted = clone(submission.draft);
    // An earlier queued acknowledgement can advance both baseline and draft.
    // That is not a local edit that should hide this later acknowledgement.
    // Compare with that acknowledged baseline, so a later local revert to the
    // original submitted value remains an edit rather than becoming clean.
    for (const key of keys || new Set([...Object.keys(submitted), ...Object.keys(saved.config)])) {
      if (same(field(previous?.config || {}, key), field(submission.baseline, key))) continue;
      if (Object.hasOwn(previous?.config || {}, key)) submitted[key] = clone(previous.config[key]);
      else delete submitted[key];
    }
    const previousDraft = panel._draft;
    const nextDraft = mergeAcknowledgedDraft(previousDraft, submitted, saved.config, keys);
    // An unrelated acknowledgement, or one submitted before the active model
    // edit, must not abandon that edit's pending defaults. A save submitting
    // the active intent still commits it and invalidates the old preparation.
    if (preservesModelPreparation(panel, submission, fields)) {
      panel._eocPendingModelPreparation?.rebindDraft?.(previousDraft, nextDraft);
    }
    panel._draft = nextDraft;
    if (full && panel._draftTitle === submission.title) panel._draftTitle = saved.title;
  }
  if (agent) {
    const selected = panel._selectedAgent?.() || {};
    // Full agent snapshots omit this optional field after the last repair.
    // Partial metadata projections do not certify configuration health.
    if (agent.subentry_id && !Object.hasOwn(agent, "configuration_issue")) delete selected.configuration_issue;
    Object.assign(selected, agent);
  }
  panel._syncConfigDirty?.();
  return true;
}

// Once the backend acknowledges a write, presentation errors cannot turn it
// into a rejected mutation or cause a replay. Feature refresh policies stay local.
export async function completeConfigurationAcknowledgement(panel, saved, submission, {message, refresh, response = saved, ...projection}) {
  if (!configurationSubmissionIsCurrent(panel, submission) || saved?.valid === false || !saved?.config) return false;
  return presentAcknowledgedMutation(panel, submission, message, async () => {
    const overtaken = configurationReadOvertookSubmission(panel, submission);
    reconcileConfigurationAcknowledgement(panel, saved, submission, projection);
    // Complete editor ownership before awaiting another read. A panel can be
    // replaced during that await; recovery must see the acknowledged identity
    // and dirty baseline, even if the refresh fails or belongs to an old visit.
    await refresh?.();
    if (overtaken && panel.isConnected !== false && configurationSubmissionIsCurrent(panel, submission)) {
      const view = panel._viewKey?.(), visit = panel._loadToken;
      await panel._loadConfigDraft?.({preserveResult:true});
      if (panel.isConnected !== false && configurationSubmissionIsCurrent(panel, submission)
          && panel._viewKey?.() === view && panel._loadToken === visit && panel._result === panel._configData) panel._render?.();
    }
  }, response);
}

export async function presentAcknowledgedMutation(panel, submission, message, refresh, result, {warning = false} = {}) {
  const current = typeof submission === "function" ? submission : () => configurationSubmissionIsCurrent(panel, submission);
  if (!current()) return false;
  let processingError = mutationResponseError(result);
  try {
    await refresh?.();
  } catch (error) {
    processingError = error;
  }
  // Notification is also presentation. Once confirmed, even a broken toast must
  // not enter a caller's rejection/retry path.
  try {
    if (current()) {
      if (processingError) panel._toast(`${message || "Changes saved"}, but some displayed information could not be refreshed: ${processingError.message || String(processingError)}`, true);
      else if (message) panel._toast(message, warning);
    }
  } catch {
    // A detached or failed renderer may have no usable notification surface.
  }
  return true;
}
