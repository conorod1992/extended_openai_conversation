import {captureConfigurationDraftOwner, captureConfigurationOwner, configurationOwnerIsCurrent, configurationSubmissionIsCurrent, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";

const VIEWS = new Set(["capabilities/guest-mode", "capabilities/quiet-hours", "capabilities/request-rules"]);
const acceptedReads = new WeakMap();
const readObligations = new WeakMap();
export const PAGE_SAVE_READ_EVENT = "eoc-page-save-read-changed";
export const PAGE_SAVE_READ_MESSAGE = "The saved policy is still being refreshed. Refresh this section before saving.";

const notifyReadState = panel => panel.shadowRoot?.dispatchEvent?.(new Event(PAGE_SAVE_READ_EVENT));

export function capturePageRead(panel, view = panel._viewKey?.()) {
  return VIEWS.has(view) ? {owner:captureConfigurationOwner(panel), view,
    visit:panel._loadToken, generation:panel._cacheGeneration} : null;
}

function currentRead(panel, read) {
  return read && panel.isConnected !== false && configurationSubmissionIsCurrent(panel, read.owner)
    && panel._viewKey?.() === read.view && panel._loadToken === read.visit
    && panel._cacheGeneration === read.generation;
}

const object = value => value && typeof value === "object" && !Array.isArray(value);
// Older Quiet Hours servers may omit revisions. Once a revision belongs to a
// policy scope, neither an acknowledgement nor a read may silently drop it.
// Quiet replaces the whole policy, so omitted known fields would become defaults.
export function completePolicyResponse(result, revisionRequired = false, knownConfig = null) {
  return object(result?.config) && (result.revision == null && !revisionRequired
    || typeof result.revision === "string" && result.revision.length > 0)
    && (!knownConfig || Object.keys(knownConfig).every(key => Object.hasOwn(result.config, key)));
}
function pageProjection(result, view, revisionRequired = false, knownConfig = null) {
  return view === "capabilities/request-rules"
    ? object(result?.defaults) && Array.isArray(result?.wording_groups)
      ? [result.defaults, result.wording_groups, result.revision] : null
    : completePolicyResponse(result, view === "capabilities/guest-mode" || revisionRequired, knownConfig) ? [result.config, result.revision] : null;
}

// Called only at accepted publication boundaries, never merely because a load
// promise resolved. Metadata can change without replacing the policy projection.
export function acceptPageRead(panel, result, read) {
  if (!currentRead(panel, read) || panel._result !== result) return false;
  const scope = panel._unsavedState?.scopes.get(read.view);
  const ownsScope = scope?.isCurrent?.();
  const projection = pageProjection(result, read.view, Boolean(ownsScope && scope.revision != null),
    ownsScope && read.view === "capabilities/quiet-hours" ? scope.baseline : null);
  // Validate within the loader's error boundary, before a read can certify a
  // replacement baseline or be used by the rendering/draft initializer.
  if (read.view !== "capabilities/request-rules") requireMutationAcknowledgement(Boolean(projection), "loading the saved policy");
  if (!projection) return false;
  acceptedReads.set(panel, {...read, projection, sequence:(acceptedReads.get(panel)?.sequence || 0) + 1});
  const obligations = readObligations.get(panel);
  for (const entry of obligations || []) {
    if (!entry.acknowledgement.owns()) obligations.delete(entry);
    else if (freshPageRead(panel, entry.state)) {
      obligations.delete(entry);
      queueMicrotask(() => entry.retry?.());
    }
  }
  notifyReadState(panel);
  return true;
}

function freshPageRead(panel, state) {
  const read = acceptedReads.get(panel);
  const projection = pageProjection(panel._result, state.view);
  return currentRead(panel, read) && configurationOwnerIsCurrent(panel, state.owner)
    && read.view === state.view && read.sequence > state.sequence
    && projection?.length === read.projection.length
    && projection.every((value, index) => value === read.projection[index]);
}

export function hasPageSaveAcknowledgement(panel) {
  return [...(panel._unsavedState?.scopes.values() || [])].some(scope => scope.acknowledgedSave?.owns());
}

export function hasPendingPageSaveRead(panel) {
  const obligations = readObligations.get(panel);
  let pending = false;
  for (const entry of obligations || []) {
    if (!entry.acknowledgement.owns() || freshPageRead(panel, entry.state)) obligations.delete(entry);
    else if (configurationSubmissionIsCurrent(panel, entry.state.owner)
        && panel._data?.is_admin && panel._viewKey?.() === entry.state.view) pending = true;
  }
  return pending;
}

// The policy save callback records this before draftScope clears pending. A
// clean detached source still owns the obligation to refresh its replacement.
export function retainPageSaveAcknowledgement(source, scope, submission, view) {
  if (source.isConnected !== false || !VIEWS.has(view)
      || !configurationSubmissionIsCurrent(source, submission)) return;
  const states = new WeakMap();
  const ownsSource = () => configurationSubmissionIsCurrent(source, submission)
    && source._unsavedState?.scopes.get(view) === scope && scope.acknowledgedSave === acknowledgement;
  const ownsDestination = panel => panel.isConnected !== false && panel._data?.is_admin
    && configurationOwnerIsCurrent(panel, submission) && panel._viewKey?.() === view;
  const acknowledgement = (destination, retry) => {
    if (!ownsSource()) return {accepted:true, refreshing:false};
    if (!ownsDestination(destination) || source.isConnected !== false) return {accepted:false, refreshing:false};
    let state = states.get(destination);
    if (state && freshPageRead(destination, state)) return {accepted:true, refreshing:false};
    if (state && !state.isCurrent()) state = null;
    if (state?.running) return {accepted:false, refreshing:true};
    if (state?.failedVisit === destination._loadToken) return {accepted:false, refreshing:false};
    const owner = captureConfigurationOwner(destination);
    // The refresh attempt retires on authentication changes. Its obligation
    // still belongs to this draft and requires a fresh accepted policy read.
    state = {view, sequence:acceptedReads.get(destination)?.sequence || 0, running:true,
      owner:captureConfigurationDraftOwner(destination),
      isCurrent:() => configurationSubmissionIsCurrent(destination, owner)};
    states.set(destination, state);
    const obligations = readObligations.get(destination) || new Set();
    obligations.add({acknowledgement, state, retry});
    readObligations.set(destination, obligations);
    notifyReadState(destination);
    destination._invalidateAfterMutation(submission.agentId,
      view === "capabilities/guest-mode" ? "guest_mode" : view === "capabilities/quiet-hours" ? "quiet_hours" : "request_rules",
      view === "capabilities/guest-mode" ? "save_policy" : view === "capabilities/quiet-hours" ? "update" : "settings");
    const stillOwned = () => ownsSource() && ownsDestination(destination) && state.isCurrent();
    void Promise.resolve().then(() => {
      if (stillOwned()) return destination._loadSection(true);
    }).catch(() => {
      // The loader may reject, or may display its own error and resolve. Both
      // require an accepted publication certificate before consuming the ACK.
    }).finally(() => {
      state.running = false;
      if (stillOwned() && !freshPageRead(destination, state)) {
        state.failedVisit = destination._loadToken;
        destination._toast?.("Changes saved, but the policy could not be refreshed. Refresh this section to retry.", true);
      }
      retry?.();
    });
    return {accepted:false, refreshing:true};
  };
  acknowledgement.owns = ownsSource;
  scope.acknowledgedSave = acknowledgement;
}
