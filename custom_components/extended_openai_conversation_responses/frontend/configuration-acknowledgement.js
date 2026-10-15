import {clone, same, mergeAcknowledgedDraft} from "./unsaved-state.js";

const field = (config, key) => Object.hasOwn(config, key) ? [config[key]] : [];

export function captureConfigurationSubmission(panel) {
  return {
    agentId:panel._agentId, entryId:panel._selectedAgent?.()?.entry_id,
    view:panel._viewKey?.(),
    revision:panel._configData?.revision,
    baseline:clone(panel._configData?.config || {}),
    draft:clone(panel._draft || {}), title:panel._draftTitle,
  };
}

export function configurationSubmissionIsCurrent(panel, submission) {
  return panel._agentId === submission.agentId
    && panel._selectedAgent?.()?.entry_id === submission.entryId;
}

// A full save replaces the saved projection. Independent saves acknowledge only
// their own fields, leaving unrelated persisted values and local edits alone.
export function reconcileConfigurationAcknowledgement(panel, saved, submission, {fields, metadata = []} = {}) {
  if (!configurationSubmissionIsCurrent(panel, submission) || saved?.valid === false || !saved?.config) return false;
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
    panel._draft = mergeAcknowledgedDraft(panel._draft, submitted, saved.config, keys);
    if (full && panel._draftTitle === submission.title) panel._draftTitle = saved.title;
  }
  if (agent) Object.assign(panel._selectedAgent?.() || {}, agent);
  panel._syncConfigDirty?.();
  return true;
}

// Once the backend acknowledges a write, presentation errors cannot turn it
// into a rejected mutation or cause a replay. Feature refresh policies stay local.
export async function completeConfigurationAcknowledgement(panel, saved, submission, {message, refresh, ...projection}) {
  if (!configurationSubmissionIsCurrent(panel, submission) || saved?.valid === false || !saved?.config) return false;
  return presentAcknowledgedMutation(panel, submission, message, async () => {
    reconcileConfigurationAcknowledgement(panel, saved, submission, projection);
    await refresh?.();
  });
}

export async function presentAcknowledgedMutation(panel, submission, message, refresh) {
  if (!configurationSubmissionIsCurrent(panel, submission)) return false;
  try {
    await refresh?.();
  } catch (error) {
    if (configurationSubmissionIsCurrent(panel, submission)) {
      panel._toast(`${message}, but some displayed information could not be refreshed: ${error.message || String(error)}`, true);
    }
    return true;
  }
  if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(message);
  return true;
}
