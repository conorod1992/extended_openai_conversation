import {clone, mergeAcknowledgedDraft} from "./unsaved-state.js";
import {pageCoordinator} from "./management-page-drafts.js";
import {recoverDisconnectedEditor} from "./management-state-safety.js";

// HA destroys custom panels during navigation and background suspension. Keep
// the owning instance in document memory, never in persistent browser storage.
// Reading it at recovery time also observes acknowledgements arriving after
// disconnection, rather than resurrecting an already-saved submission.
const retained = new Map();
const ownScopes = panel => [...pageCoordinator(panel).scopes.entries()]
  .filter(([id]) => id !== "retained-drafts").map(([, scope]) => scope);
const hasOwnChanges = panel => ownScopes(panel).some(scope => scope.dirty());
let unloadBound = false;

function hasRetainedChanges() {
  return [...retained].some(([key, sources]) => [...sources].some(source => owner(source) === key && hasOwnChanges(source)));
}

function retainedBeforeUnload(event) {
  if (!hasRetainedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
}

function synchronizeUnloadGuard() {
  const dirty = hasRetainedChanges();
  if (dirty === unloadBound) return;
  if (dirty) window.addEventListener("beforeunload", retainedBeforeUnload);
  else window.removeEventListener("beforeunload", retainedBeforeUnload);
  unloadBound = dirty;
}

function owner(panel) {
  const user = panel.hass?.user?.id;
  const agent = panel._selectedAgent?.();
  return user && agent?.entry_id && agent?.subentry_id
    ? JSON.stringify([user, agent.entry_id, agent.subentry_id]) : null;
}

export function retainPanelDrafts(panel) {
  const key = owner(panel);
  if (!key) return;
  const sources = retained.get(key) || new Set();
  if (hasOwnChanges(panel)) sources.add(panel);
  else sources.delete(panel);
  if (sources.size) retained.set(key, sources);
  else retained.delete(key);
  synchronizeUnloadGuard();
}

export function restorePanelDrafts(panel) {
  const key = owner(panel), sources = retained.get(key);
  for (const source of sources || []) {
    if (owner(source) !== key) { sources.delete(source); continue; }
    restoreDraftSource(panel, source, key);
    if (!hasOwnChanges(source) && !source._eocAcknowledgedCollection) sources.delete(source);
  }
  if (sources?.size) pageCoordinator(panel).register("retained-drafts", {
    dirty: () => owner(panel) === key && [...sources].some(source => source !== panel && hasOwnChanges(source)),
    owns: destination => Boolean(destination),
    destinations: () => [...sources].filter(hasOwnChanges).map(source => source._viewKey()),
    get pending() { return [...sources].some(source => ownScopes(source).some(scope => scope.pending)); },
    discard: () => {
      for (const source of sources) for (const scope of ownScopes(source)) if (scope.dirty()) scope.discard?.();
      sources.clear();
      retained.delete(key);
      synchronizeUnloadGuard();
    },
  });
  if (!sources?.size) retained.delete(key);
  synchronizeUnloadGuard();
}

function restoreDraftSource(panel, source, key) {
  if (!source || owner(source) !== key || source.isConnected || !panel.isConnected || panel._busy || panel._error) return;
  // Do not transfer ownership of a mutation still awaiting acknowledgement.
  if ([source, panel].some(value => value._configurationSaving || value._eocAgentMutations
      || [...pageCoordinator(value).scopes.values()].some(scope => scope.pending))) {
    if (!panel._eocDraftRecoveryTimer) panel._eocDraftRecoveryTimer = setTimeout(() => {
      panel._eocDraftRecoveryTimer = null;
      restorePanelDrafts(panel);
    }, 100);
    return;
  }
  const acknowledgement = source._eocAcknowledgedCollection;
  if (acknowledgement) {
    const result = acknowledgement(panel);
    if (result.accepted && source._eocAcknowledgedCollection === acknowledgement) source._eocAcknowledgedCollection = null;
    // Publish an authoritative collection before reopening an editor against
    // its newly acknowledged identity/revision. Other routes only invalidate.
    if (result.refreshing) return;
  }
  if (source._configDirty && source._draftAgentId === panel._agentId && panel._data?.is_admin) {
    if (!panel._configDirty) {
      const fresh = panel._configData?.config ? panel._configData : null;
      panel._configData = fresh || clone(source._configData);
      panel._configDataStale = !fresh;
      panel._draft = mergeAcknowledgedDraft(source._draft, source._configData.config, panel._configData.config);
      panel._draftTitle = source._draftTitle !== source._configData.title ? source._draftTitle : panel._configData.title;
      panel._draftAgentId = panel._agentId;
      source._eocPendingModelPreparation?.transferTo?.(panel);
      panel._syncConfigDirty();
      source._setConfigDirty(false);
      panel._render();
    }
  }
  for (const scope of ownScopes(source)) if (scope.dirty() && scope.restore) scope.restore(panel);
  // Page drafts and editors keep their original route, baseline and revision.
  // Restore only after that route has passed the new panel's access checks.
  if (source._viewKey() === panel._viewKey() && source._scopeId === panel._scopeId
      && (panel._viewKey() !== "data-memory/memories" || source._memoryKind === panel._memoryKind)) {
    const from = pageCoordinator(source).scopes.get(source._viewKey());
    const to = pageCoordinator(panel).scopes.get(panel._viewKey());
    if (from?.dirty() && to?.write && !to.dirty() && panel._data?.is_admin) {
      const field = {"capabilities/guest-mode":"_guestDraft", "capabilities/quiet-hours":"_quietHoursDraft", "capabilities/request-rules":"_rulesSettingsDraft"}[panel._viewKey()];
      if (field) {
        // A recovered policy keeps the revision against which its edits were
        // authored. Rebasing onto a fresh read would bypass conflict checks.
        to.baseline = clone(from.baseline);
        to.revision = from.revision;
        to.write(clone(source[field]));
        from.discard();
        panel._render();
      }
    }
    // A newer HA panel can arrive while an older destination awaits imports.
    // Retry through the complete lifecycle gate, including ownership, access,
    // pending mutations and the current route, rather than bypassing it.
    void recoverDisconnectedEditor(panel, source, () => restorePanelDrafts(panel)).catch(() => panel._toast?.("Unsaved editor recovery is pending. Return to its original page to retry.", true));
  }
}

export function stopDraftRecovery(panel) {
  clearTimeout(panel._eocDraftRecoveryTimer);
  panel._eocDraftRecoveryTimer = null;
}
