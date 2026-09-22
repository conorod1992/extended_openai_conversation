import {saveConfiguration} from "./management-actions.js";
import {enhancementChanged} from "./management-enhancement-state.js";
import {UnsavedState, clone, same, draftScope, saveBarMarkup} from "./unsaved-state.js";

const GUEST = "capabilities/guest-mode";
const QUIET = "capabilities/quiet-hours";
const RULES = "capabilities/request-rules";
const settings = (result) => ({defaults: clone(result.defaults || {}), wording_groups: clone(result.wording_groups || [])});

export function pageCoordinator(panel) {
  if (!panel._unsavedState) panel._unsavedState = new UnsavedState();
  const state = panel._unsavedState;
  if (!state.scopes.has("configuration")) state.register("configuration", {
    dirty: () => Boolean(panel._configDirty),
    get pending() { return Boolean(panel._configurationSaving); },
    save: () => saveConfiguration(panel, panel.shadowRoot?.querySelector("#save-config")),
    destinations: () => [...(panel._configurationDirtyDestinations?.() || [])],
    owns: (destination) => Boolean(destination && panel._isDraftView?.(...destination.split("/"))),
    discard: () => {
      if (panel._configData) {
        panel._draft = clone(panel._configData.config);
        panel._draftTitle = panel._configData.title;
      }
      panel._setConfigDirty(false);
    },
  });
  return state;
}

export function currentPageScope(panel) {
  return pageCoordinator(panel).scopes.get(panel._viewKey?.());
}

export function initializePageDraft(panel) {
  const state = pageCoordinator(panel), view = panel._viewKey?.(), result = panel._result;
  if (![GUEST, QUIET, RULES].includes(view) || !result || panel._busy || panel._error) return;
  const existing = state.scopes.get(view);
  if (existing?.agent === panel._agentId) {
    if (view === RULES && existing.result !== result && same(existing.baseline, settings(result))) existing.revision = result.revision;
    if (existing.result === result || existing.dirty() || existing.pending) return;
  }
  const field = view === GUEST ? "_guestDraft" : view === QUIET ? "_quietHoursDraft" : "_rulesSettingsDraft";
  const baseline = view === RULES ? settings(result) : result.config || {};
  panel[field] = clone(baseline);
  const scope = draftScope({
    baseline, read: () => panel[field], write: (value) => { panel[field] = value; },
    owns: (destination) => destination === view,
    destinations: () => [view],
    save: async (submitted, current) => {
      if (view === RULES) return saveRuleSettings(panel, submitted, current);
      const saved = await panel._call(view === GUEST ? "guest_mode" : "quiet_hours", view === GUEST ? "save_policy" : "update", {
        config: submitted, ...(current.revision ? {revision: current.revision} : {}),
      });
      panel._result = {...panel._result, ...saved, ...(view === GUEST ? {legacy_policy: false, migration_notice: null} : {})};
      current.result = panel._result;
      current.revision = saved.revision;
      if (view === GUEST) { panel._guestMigrationReview = false; panel._guestStartingFresh = false; }
      return saved.config;
    },
  });
  Object.assign(scope, {agent: panel._agentId, result, revision: result.revision});
  state.register(view, scope);
}

async function saveRuleSettings(panel, submitted, scope) {
  // Each acknowledged write advances only its own baseline and the shared CAS
  // revision. A failure leaves the remaining draft dirty; never retry a write
  // against an unverified newer revision or report a partial save as success.
  let completed = 0;
  try {
    for (const key of ["defaults", "wording_groups"]) {
      if (same(submitted[key], scope.baseline[key])) continue;
      const result = await panel._call("request_rules", key, {[key]: submitted[key], revision: scope.revision});
      scope.baseline[key] = clone(result[key]);
      scope.revision = result.revision;
      panel._result = {...panel._result, [key]: clone(result[key]), revision: result.revision};
      scope.result = panel._result;
      completed++;
    }
    return clone(scope.baseline);
  } catch (err) {
    throw new Error(`${completed ? "Some settings were saved; the remaining changes are still unsaved. " : ""}${err.message || String(err)}`);
  }
}

export function readRuleSettings(panel) {
  if (panel._viewKey?.() !== RULES || !panel._rulesSettingsDraft) return;
  const root = panel.shadowRoot, q = (selector) => root.querySelector(selector);
  if (!q("#rules-default-word-forms")) return;
  panel._rulesSettingsDraft = {
    defaults: {
      word_forms: q("#rules-default-word-forms").checked,
      wording_alternatives: q("#rules-default-wording").checked,
      fuzzy: q("#rules-default-fuzzy").checked,
      fuzzy_threshold: Number(q("#rules-default-threshold").value),
    },
    wording_groups: [...root.querySelectorAll(".wording-group")].map((row) => ({
      canonical: row.querySelector(".wording-canonical").value.trim(),
      alternatives: row.querySelector(".wording-alternatives").value.split(",").map((value) => value.trim()).filter(Boolean),
    })),
  };
}

export function refreshPageSaveBar(panel) {
  const scope = currentPageScope(panel), root = panel.shadowRoot;
  if (!scope || !root?.querySelector) return;
  const dirty = scope.dirty();
  if (!enhancementChanged(panel, "page-save-bar", [scope, dirty, scope.pending])) return;
  let bar = root.querySelector(".save-bar");
  if (!dirty) { bar?.remove(); root.dispatchEvent?.(new Event("eoc-config-dirty-changed")); return; }
  if (!bar) {
    root.querySelector("main")?.insertAdjacentHTML("beforeend", saveBarMarkup(scope));
    bar = root.querySelector(".save-bar");
  }
  const save = bar?.querySelector("#save-page"), discard = bar?.querySelector("#discard-page");
  if (save) { save.disabled = scope.pending; save.textContent = scope.pending ? "Saving…" : "Save changes"; }
  if (discard) discard.disabled = scope.pending;
  root.dispatchEvent?.(new Event("eoc-config-dirty-changed"));
}

function renderSavedDraft(panel, force = false) {
  const root = panel.shadowRoot;
  const expanded = [...root.querySelectorAll("main details")].map((details) => details.open);
  const focus = root.activeElement;
  const position = {x: window.scrollX, y: window.scrollY};
  const selection = focus && typeof focus.selectionStart === "number" ? [focus.selectionStart, focus.selectionEnd] : null;
  if (force) panel._eocMainMarkup = null;
  panel._render();
  root.querySelectorAll("main details").forEach((details, index) => { details.open = expanded[index] ?? details.open; });
  const nextFocus = focus?.id ? root.getElementById(focus.id) : null;
  nextFocus?.focus({preventScroll: true});
  if (nextFocus && selection) nextFocus.setSelectionRange(...selection);
  window.scrollTo(position.x, position.y);
}

export async function savePageChanges(panel) {
  const scope = currentPageScope(panel);
  if (!scope || scope.pending || !scope.dirty()) return false;
  const operation = scope.save();
  refreshPageSaveBar(panel);
  try {
    await operation;
    // Re-project authoritative status and normalized fields without a data reload.
    renderSavedDraft(panel);
    panel._toast("Changes saved");
    return true;
  } catch (err) {
    panel._toast(`Unable to save changes: ${err.message || String(err)}`, true);
    return false;
  } finally { refreshPageSaveBar(panel); }
}

export function bindPageDrafts(panel) {
  const root = panel.shadowRoot;
  if (!root || root.__eocPageDraftsBound) return;
  root.__eocPageDraftsBound = true;
  const sync = () => {
    if (![GUEST, QUIET, RULES].includes(panel._viewKey?.())) return;
    queueMicrotask(() => {
      if (![GUEST, QUIET, RULES].includes(panel._viewKey?.())) return;
      readRuleSettings(panel);
      refreshPageSaveBar(panel);
    });
  };
  for (const type of ["input", "change", "value-changed"]) root.addEventListener(type, sync);
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    if (button?.id === "save-page") void savePageChanges(panel);
    if (button?.id === "discard-page") {
      const scope = currentPageScope(panel);
      if (scope?.pending) return;
      scope?.discard();
      renderSavedDraft(panel, true);
    }
    if (button?.matches("#wording-add,.wording-remove")) sync();
  });
}
