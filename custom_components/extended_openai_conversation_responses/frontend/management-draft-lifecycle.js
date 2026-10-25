import {clone, same} from "./unsaved-state.js";
import {pageCoordinator} from "./management-page-drafts.js";
import {recoverDisconnectedEditor, showRecoveredDraftCopy} from "./management-state-safety.js";
import {mergeConfigurationRead, configurationDraftBaseline} from "./configuration-acknowledgement.js";
import {planConfigurationRecovery, combineRecoveredConfiguration, recoveredConfigurationChanges} from "./configuration-draft-recovery.js";

// HA destroys custom panels during navigation and background suspension. Keep
// the owning instance in document memory, never in persistent browser storage.
// Reading it at recovery time also observes acknowledgements arriving after
// disconnection, rather than resurrecting an already-saved submission.
const retained = new Map();
const recoveryConfigurationReads = new WeakMap();
const recoveryConfigurationCopies = new WeakMap();
const ownScopes = panel => [...pageCoordinator(panel).scopes.entries()]
  .filter(([id]) => id !== "retained-drafts").map(([, scope]) => scope);
const hasOwnChanges = panel => ownScopes(panel).some(scope => scope.dirty());
const hasOwnPendingWork = panel => Boolean(panel._configurationSaving || panel._eocAgentMutations
  || ownScopes(panel).some(scope => scope.pending));
const hasRetainedWork = panel => hasOwnChanges(panel) || hasOwnPendingWork(panel)
  || Boolean(panel._eocAcknowledgedCollection || panel._eocAcknowledgedConfiguration);
let unloadBound = false;

function hasRetainedChanges() {
  return [...retained].some(([key, sources]) => [...sources].some(source => owner(source) === key && (hasOwnChanges(source) || hasOwnPendingWork(source))));
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
  if (hasRetainedWork(panel)) sources.add(panel);
  else sources.delete(panel);
  if (sources.size) retained.set(key, sources);
  else retained.delete(key);
  synchronizeUnloadGuard();
}

export function restorePanelDrafts(panel) {
  const key = owner(panel), sources = retained.get(key);
  const previousCopy = recoveryConfigurationCopies.get(panel);
  if (previousCopy && (!previousCopy.source._configDirty || owner(previousCopy.source) !== key)) recoveryConfigurationCopies.delete(panel);
  for (const source of sources || []) {
    if (owner(source) !== key) { sources.delete(source); continue; }
    restoreDraftSource(panel, source, key);
    if (!hasRetainedWork(source)) sources.delete(source);
  }
  if (sources?.size) pageCoordinator(panel).register("retained-drafts", {
    dirty: () => owner(panel) === key && [...sources].some(source => source !== panel && hasOwnChanges(source)),
    owns: destination => Boolean(destination),
    destinations: () => [...sources].filter(hasOwnChanges).flatMap(source => ownScopes(source)
      .filter(scope => scope.dirty()).flatMap(scope => scope.destinations?.() || [source._viewKey()])),
    // A dispatched write remains pending after HA replaces its owning panel.
    // Discarding the detached editor cannot cancel that backend operation.
    get pending() { return [...sources].some(hasOwnPendingWork); },
    discard: () => {
      for (const source of sources) for (const scope of ownScopes(source)) if (scope.dirty()) scope.discard?.();
      sources.clear();
      retained.delete(key);
      recoveryConfigurationCopies.delete(panel);
      synchronizeUnloadGuard();
    },
  });
  if (!sources?.size) retained.delete(key);
  synchronizeUnloadGuard();
}

function restoreDraftSource(panel, source, key) {
  if (!source || owner(source) !== key || source.isConnected || !panel.isConnected || panel._busy || panel._error) return;
  // Do not transfer ownership of a mutation still awaiting acknowledgement.
  if ([source, panel].some(value => hasOwnPendingWork(value)
      || pageCoordinator(value).scopes.get("retained-drafts")?.pending)) {
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
  const configurationAcknowledgement = source._eocAcknowledgedConfiguration;
  if (configurationAcknowledgement) {
    const result = configurationAcknowledgement(panel, () => restorePanelDrafts(panel));
    if (!result.accepted) return;
    if (source._eocAcknowledgedConfiguration === configurationAcknowledgement) source._eocAcknowledgedConfiguration = null;
  }
  if (source._configDirty && source._draftAgentId === panel._agentId && panel._data?.is_admin) {
    if (panel._configDirty && panel._draftAgentId !== panel._agentId) return;
    // A cold Retention visit has only a partial configuration projection.
    // Missing fields there are unknown, not deletions from a retained full
    // draft. Obtain a full, current baseline before transferring ownership.
    if (prepareFullRecoveryBaseline(panel, source, key)) return;
    const fresh = panel._configData?.config ? panel._configData : null;
    const target = {draft:panel._draft, title:panel._draftTitle};
    const targetBaseline = configurationDraftBaseline(panel);
    const sourceBaseline = configurationDraftBaseline(source);
    const targetOwnsModel = Boolean(panel._eocModelIntent || panel._eocPendingModelPreparation || panel._eocReasoningChoiceRevision);
    const plan = (panel._configDirty || targetOwnsModel) && target.draft && targetBaseline?.config
      ? planConfigurationRecovery({draft:source._draft, title:source._draftTitle}, sourceBaseline, target, targetBaseline, {targetOwnsModel}) : null;
    if (plan?.conflicts.length) {
      showConfigurationRecoveryConflict(panel, source, key, sourceBaseline);
      return;
    }
    panel._configData = fresh || clone(source._configData);
    panel._configDataStale = !fresh;
    const sourceDraft = source._draft;
    const merged = mergeConfigurationRead(source, {draft:sourceDraft, title:source._draftTitle}, source._configData, panel._configData);
    source._eocPendingModelPreparation?.rebindDraft?.(sourceDraft, sourceDraft,
      {savedConfig:panel._configData.projection === "retention" ? undefined : panel._configData.config});
    const recovered = plan ? combineRecoveredConfiguration(target, targetBaseline, merged) : merged;
    if (plan?.preserveTargetModel) panel._eocPendingModelPreparation?.rebindDraft?.(target.draft, recovered.draft);
    panel._draft = recovered.draft;
    panel._draftTitle = recovered.title;
    panel._draftAgentId = panel._agentId;
    if (!plan?.preserveTargetModel) {
      if (source._eocPendingModelPreparation) source._eocPendingModelPreparation.transferTo?.(panel);
      else source._eocModelIntent?.transferTo?.(source, panel);
    }
    panel._syncConfigDirty();
    source._setConfigDirty(false);
    if (recoveryConfigurationCopies.get(panel)?.source === source) recoveryConfigurationCopies.delete(panel);
    panel._render();
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
        if (field === "_guestDraft") {
          // Keep the recovered legacy policy editable. Its authoring mode is
          // transferred with the same owner, access and revision gates.
          panel._guestMigrationReview = Boolean(source._guestMigrationReview);
          panel._guestStartingFresh = Boolean(source._guestStartingFresh);
        }
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

function showConfigurationRecoveryConflict(panel, source, key, baseline) {
  const baselineSnapshot = clone(baseline);
  const current = {draft:clone(source._draft), title:source._draftTitle};
  const values = recoveredConfigurationChanges(current, baseline);
  const previous = recoveryConfigurationCopies.get(panel);
  if (previous?.source === source && previous.visit === panel._loadToken && same(previous.values, values)) return;
  if (panel.shadowRoot?.querySelector("dialog[open]")) return;
  const visit = panel._loadToken, generation = panel._cacheGeneration;
  recoveryConfigurationCopies.set(panel, {source, visit, values});
  showRecoveredDraftCopy(panel, {
    title:"Recovered unsaved configuration", label:"Recovered configuration values", values,
    description:"The retained configuration conflicts with your current draft. Copy its recovered changes before discarding either draft.",
    canDiscard:() => panel.isConnected && !source.isConnected && panel._data?.is_admin && owner(panel) === key && owner(source) === key
      && panel._loadToken === visit && panel._cacheGeneration === generation
      && same(source._draft, current.draft) && source._draftTitle === current.title
      && same(configurationDraftBaseline(source), baselineSnapshot),
    onDiscard:() => {
      pageCoordinator(source).scopes.get("configuration")?.discard();
      recoveryConfigurationCopies.delete(panel);
      retainPanelDrafts(source);
      queueMicrotask(() => restorePanelDrafts(panel));
    },
  });
}

function prepareFullRecoveryBaseline(panel, source, key) {
  if (!source._configData?.config || source._configData.projection === "retention"
      || panel._configData?.projection !== "retention") return false;
  const previous = recoveryConfigurationReads.get(panel);
  if (previous?.key === key && (previous.running || (previous.visit === panel._loadToken && previous.generation === panel._cacheGeneration))) return true;
  const state = {key, running:true, visit:panel._loadToken, generation:panel._cacheGeneration, view:panel._viewKey()};
  recoveryConfigurationReads.set(panel, state);
  void Promise.resolve().then(() => {
    if (panel.isConnected && owner(panel) === key && panel._data?.is_admin && panel._viewKey() === state.view
        && panel._loadToken === state.visit && panel._cacheGeneration === state.generation) return panel._loadConfigDraft({preserveResult:true, full:true});
  }).catch(() => {
    // Keep the original source intact and avoid retrying a failed read on every
    // render. A new visit or invalidation can retry through the normal gates.
    if (panel.isConnected && owner(panel) === key && panel._loadToken === state.visit) {
      panel._toast?.("Unsaved configuration is retained. Refresh this section to retry loading the complete settings.", true);
    }
  }).finally(() => {
    state.running = false;
    if (panel.isConnected && owner(panel) === key && (panel._configData?.projection !== "retention"
        || panel._loadToken !== state.visit || panel._cacheGeneration !== state.generation)) restorePanelDrafts(panel);
  });
  return true;
}

export function stopDraftRecovery(panel) {
  clearTimeout(panel._eocDraftRecoveryTimer);
  panel._eocDraftRecoveryTimer = null;
}
