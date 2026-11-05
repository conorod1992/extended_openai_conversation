// Core dialog handlers are delegated from the host so owned dialogs can be
// created on demand and removed when their route or agent changes.
import {clone, same} from "./unsaved-state.js";
import {captureConfigurationOwner, captureConfigurationDraftOwner, configurationSubmissionIsCurrent} from "./configuration-acknowledgement.js";

export function setDialogBaseline(panel, id, value) {
  (panel._eocDialogBaselines ||= new Map()).set(id, value);
}

export function editorControls(dialog) {
  return [...dialog.querySelectorAll("input,select,textarea,ha-selector")]
    .filter(control => !["button", "submit", "reset", "file", "search"].includes(control.type));
}

// This is the authored control state, including unparsed native YAML. Typed
// Memory/Knowledge values remain separate from this lossless recovery payload.
export function dialogState(dialog) {
  if (!dialog) return [];
  if (dialog.__eocReadDraft) return dialog.__eocReadDraft();
  return editorControls(dialog).map(control => {
    const isSelector = String(control.tagName || "").toUpperCase() === "HA-SELECTOR";
    return {
      id:control.id || control.name || "", type:control.type || control.tagName,
      checked:"checked" in control ? Boolean(control.checked) : undefined,
      value:isSelector ? clone(control.value ?? null) : control.value,
      ...(isSelector && control.__eocYamlDraft?.().length ? {yaml:control.__eocYamlDraft()} : {}),
    };
  });
}

export const PERSISTENT_DIALOGS = new Set([
  "knowledge-dialog", "memory-dialog", "session-dialog", "reassign-dialog", "confirm-dialog",
]);
const RETAINED_DIALOGS = new Set([...PERSISTENT_DIALOGS, "temporary-memory-dialog"]);

export function bindPanelDialogs(panel) {
  const root = panel.shadowRoot;
  if (root.__eocDialogsBound) return;
  root.__eocDialogsBound = true;
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    const dialog = button?.closest?.("dialog");
    if (!PERSISTENT_DIALOGS.has(dialog?.id)) return;
    const actions = {
      "confirm-cancel": () => panel._resolveConfirm(false),
      "confirm-accept": () => panel._resolveConfirm(true),
      "knowledge-delete": () => panel._deleteSource(panel._editingSource?.source_id, true, button),
      "memory-delete": () => panel._deleteMemory(panel._editingMemory?.memory_id, true, button),
      "reassign-cancel": () => dialog.close(),
      "reassign-save": () => panel._saveReassign(button),
    };
    if (actions[button.id]) actions[button.id]();
    else if (button.classList.contains("close-editor")) {
      if (button.classList.contains("icon")) panel._requestEditorClose();
      else dialog.close();
    }
    else if (button.classList.contains("close-session")) dialog.close();
  });
  root.addEventListener("submit", (event) => {
    if (!["knowledge-form", "memory-form"].includes(event.target?.id)) return;
    event.preventDefault();
    if (event.target.id === "knowledge-form") void panel._saveKnowledge();
    else void panel._saveMemory();
  });
  root.addEventListener("input", (event) => {
    if (event.target?.id === "knowledge-content") panel._updateKnowledgeCounter();
  });
  root.addEventListener("cancel", (event) => {
    const dialog = event.target;
    if (!PERSISTENT_DIALOGS.has(dialog?.id)) return;
    event.preventDefault();
    if (dialog.id === "confirm-dialog") panel._resolveConfirm(false);
    else if (["knowledge-dialog", "memory-dialog"].includes(dialog.id)) panel._requestEditorClose();
    else dialog.close();
  }, true);
}

export function updateDialogs(panel, markup, {preserveEditors = false} = {}) {
  const root = panel.shadowRoot;
  const host = root.querySelector("#eoc-dialog-host");
  if (!host) return;
  const template = document.createElement("template");
  template.innerHTML = markup;
  const desired = new Set([...template.content.children].map(child => child.id));
  for (const child of [...host.children]) {
    if (!desired.has(child.id) || (!preserveEditors && !RETAINED_DIALOGS.has(child.id))) {
      // Release radio-group ownership before detaching the dialog subtree.
      // Chromium can otherwise retain discarded import dialogs in its native
      // shadow-root group registry even after JavaScript garbage collection.
      child.querySelectorAll('input[type="radio"][name]').forEach(input => { input.name = ""; });
      child.remove();
    }
  }
  for (const child of [...template.content.children]) {
    if (!host.querySelector(`#${child.id}`)) host.append(child);
  }
}

export function knowledgeSourceAvailabilityControl() {
  return `<div class="config-toggle setting knowledge-source-availability-setting"><span class="setting-copy"><span class="setting-label-row"><label for="knowledge-source-enabled"><strong>Available to the assistant</strong></label></span><small>Turn this off to keep the source stored locally without including it in Knowledge retrieval.</small></span><label class="switch-control" for="knowledge-source-enabled"><input id="knowledge-source-enabled" type="checkbox" role="switch" checked><span class="switch-track" aria-hidden="true"></span></label></div>`;
}


export function showRecoveredDraftCopy(panel, {
  title = "Recovered unsaved editor",
  description = "The saved definition could not be safely reopened. Copy your recovered edits before opening the current definition.",
  values,
  label = "Recovered editor values",
  canDiscard = () => false,
  onDiscard = () => {},
  onKeep = () => {},
}) {
  const recovery = document.createElement("dialog");
  recovery.className = "editor-dialog";
  recovery.dataset.eocRecoveredEditor = "";
  const heading = document.createElement("h2");
  heading.textContent = title;
  const explanation = document.createElement("p");
  explanation.textContent = description;
  const text = document.createElement("textarea");
  text.value = JSON.stringify(values, null, 2);
  text.readOnly = true;
  text.setAttribute("aria-label", label);
  const close = document.createElement("button");
  close.textContent = "Keep draft for later";
  close.onclick = () => {onKeep();recovery.close();};
  const discard = document.createElement("button");
  discard.textContent = "Discard recovered edits";
  discard.onclick = () => {
    if (recovery.open && canDiscard()) onDiscard();
    recovery.close();
  };
  recovery.addEventListener("close", () => {
    close.onclick = discard.onclick = null;
    recovery.remove();
  }, {once:true});
  recovery.append(heading, explanation, text, close, discard);
  panel.shadowRoot.append(recovery);
  recovery.showModal();
  return recovery;
}

const recoveredScopeCopies = new WeakMap();
const competingScopes = new WeakSet();
export const scopeNeedsRecoveryReview = scope => Boolean(scope && competingScopes.has(scope));
export const clearScopeRecoveryReview = scope => {if (scope) competingScopes.delete(scope);};

// Competing policy, interval and Voice drafts keep their own baselines and revisions.
// Review both copies instead of silently dropping or rebasing either owner.
export function reviewRecoveredScope(panel, source, id, label, retry, {targetId = id, requireReview = false} = {}) {
  const from = source._unsavedState?.scopes.get(id), to = panel._unsavedState?.scopes.get(targetId);
  if (!(from?.dirty() || from?.hasRecoveryIntent?.()) || !(requireReview || to?.dirty() || to?.hasRecoveryIntent?.() || scopeNeedsRecoveryReview(from)) || !from?.isCurrent?.() || !to?.isCurrent?.()) return;
  const owner = captureConfigurationOwner(panel), sourceOwner = captureConfigurationDraftOwner(source);
  if (["agentId", "entryId", "userId"].some(key => owner[key] !== sourceOwner[key])) return;
  const view = panel._viewKey(), visit = panel._loadToken, generation = panel._cacheGeneration;
  const current = () => panel.isConnected && !source.isConnected && panel._data?.is_admin && !panel._eocPresentationBlocked
    && configurationSubmissionIsCurrent(panel, owner) && configurationSubmissionIsCurrent(source, sourceOwner)
    && panel._viewKey() === view && panel._loadToken === visit && panel._cacheGeneration === generation
    && source._unsavedState?.scopes.get(id) === from && panel._unsavedState?.scopes.get(targetId) === to
    && from.isCurrent() && to.isCurrent();
  if (!current()) return;
  const values = {recovered:clone(from.read()), current:clone(to.read())};
  const baseline = clone(from.baseline), targetBaseline = clone(to.baseline);
  const copies = recoveredScopeCopies.get(panel) || new WeakMap();
  recoveredScopeCopies.set(panel, copies);
  const previous = copies.get(from);
  // Keep dismisses this source for the visit while the destination is edited.
  // Source changes or a later visit still require a fresh review.
  if (previous && configurationSubmissionIsCurrent(panel, previous.owner) && previous.visit === visit
      && previous.generation === generation && same(previous.values.recovered, values.recovered) && same(previous.baseline, baseline)
      && (previous.kept || same(previous.values.current, values.current) && same(previous.targetBaseline, targetBaseline))) return;
  if (panel.shadowRoot?.querySelector("dialog[open]")) return;
  // A competing draft remains a review copy after Keep, including when the
  // destination subsequently saves and becomes clean. Clean is not consent
  // to replace that newly saved intent with the older retained values.
  competingScopes.add(from);
  const copy = {owner, visit, generation, values, baseline, targetBaseline, kept:false};
  copies.set(from, copy);
  const dialog = showRecoveredDraftCopy(panel, {
    title:`Recovered ${label}`, label:`Recovered and current ${label} values`, values,
    description:"Another editor has unsaved changes. Both drafts are kept. Copy the recovered values before discarding either draft.",
    canDiscard:() => current() && same(from.read(), values.recovered) && same(to.read(), values.current)
      && same(from.baseline, baseline) && same(to.baseline, targetBaseline),
    onDiscard:() => {competingScopes.delete(from);from.discard();},
    onKeep:() => {if (current()) copy.kept = true;},
  });
  // A read or navigation may retire this dialog. Retry through the lifecycle's
  // current owner gates so a stale copy cannot hide another retained scope.
  dialog.addEventListener("close", () => queueMicrotask(() => {if (panel.isConnected) retry?.();}), {once:true});
}
