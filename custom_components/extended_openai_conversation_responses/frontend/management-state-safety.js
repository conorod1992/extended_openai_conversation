import {getRouteFeature} from "./management-route.js";
import {pageCoordinator, currentPageScope} from "./management-page-drafts.js";
import {same, clone} from "./unsaved-state.js";
import {SECTION_CACHE_TTL_MS} from "./management-cache.js";
export {SECTION_CACHE_TTL_MS};

const RESET_PROMPT_KEYS = [
  "prompt",
  "current_datetime_enabled",
  "exposed_entities_enabled",
  "current_datetime_template",
  "exposed_entities_template",
];
const RESET_ADVANCED_KEYS = [
  "temperature",
  "top_p",
  "reasoning_effort",
  "service_tier",
  "shorten_tool_call_id",
  "memory_auto_retrieve_limit",
  "memory_retrieval_mode",
  "memory_embedding_model",
];
const RESET_MODEL_KEYS = [
  "temperature",
  "top_p",
  "reasoning_effort",
  "service_tier",
  "shorten_tool_call_id",
];

function configBaselineReady(panel) {
  return Boolean(
    panel?._configData?.config
      && panel?._draft
      && panel._draftAgentId === panel._agentId
  );
}

function configKeyChanged(panel, key) {
  if (!configBaselineReady(panel)) return false;
  if (key === "__title") return panel._draftTitle !== panel._configData.title;
  return !same(panel._draft[key], panel._configData.config[key]);
}

export function rebuildConfigDirtyKeys(panel) {
  const changed = new Set();
  if (configBaselineReady(panel)) {
    const baseline = panel._configData.config;
    const draft = panel._draft;
    const keys = new Set([...Object.keys(baseline), ...Object.keys(draft)]);
    for (const key of keys) {
      if (!same(baseline[key], draft[key])) changed.add(key);
    }
    if (panel._draftTitle !== panel._configData.title) changed.add("__title");
  }
  panel._eocDirtyConfigKeys = changed;
  return changed;
}

export function syncConfigDirtyKeys(panel, keys) {
  if (!configBaselineReady(panel)) {
    panel._eocDirtyConfigKeys = new Set();
    return panel._eocDirtyConfigKeys;
  }
  const changed = panel._eocDirtyConfigKeys instanceof Set
    ? panel._eocDirtyConfigKeys
    : new Set();
  for (const key of keys) {
    if (!key) continue;
    if (configKeyChanged(panel, key)) changed.add(key);
    else changed.delete(key);
  }
  panel._eocDirtyConfigKeys = changed;
  return changed;
}

export function configKeysForButton(button) {
  if (!button) return [];
  if (button.id === "reset-prompt") return RESET_PROMPT_KEYS;
  if (button.id === "reset-advanced") return RESET_ADVANCED_KEYS;
  if (button.id === "reset-model-parameters") return RESET_MODEL_KEYS;
  if (button.classList?.contains("reset-context-template")) {
    return button.dataset?.templateKey ? [button.dataset.templateKey] : [];
  }
  if (
    button.id === "add-regex"
    || button.classList?.contains("delete-regex")
    || button.classList?.contains("move-regex")
  ) {
    return ["speech_regex_replacements"];
  }
  return [];
}

function restoreConfigFocus(panel, control) {
  const id = control?.id;
  if (!id || typeof requestAnimationFrame !== "function") return;
  requestAnimationFrame(() => {
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(id) : id;
    panel.shadowRoot?.querySelector?.(`#${escaped}`)?.focus?.({preventScroll: true});
  });
}

export function applyTargetedConfigDirty(panel, keys, control = null, renderWhenClean = true) {
  if (!keys.length) return;
  const wasDirty = Boolean(panel._configDirty);
  const changed = syncConfigDirtyKeys(panel, keys);
  panel._setConfigDirty(changed.size > 0);
  panel.shadowRoot?.dispatchEvent?.(new Event("eoc-config-dirty-changed"));
  if (renderWhenClean && wasDirty && !panel._configDirty) {
    panel._render?.();
    restoreConfigFocus(panel, control);
  }
}

function dialogState(dialog) {
  if (!dialog) return [];
  return [...dialog.querySelectorAll("input,select,textarea,ha-selector")]
    .filter((control) => !["button", "submit", "reset", "file", "search"].includes(control.type))
    .map((control) => {
      const isSelector = String(control.tagName || "").toUpperCase() === "HA-SELECTOR";
      return {
        id: control.id || control.name || "",
        type: control.type || control.tagName,
        checked: "checked" in control ? Boolean(control.checked) : undefined,
        value: isSelector ? clone(control.value ?? null) : control.value,
      };
    });
}


export function dialogHasUnsavedChanges(dialog, baseline, toolInitialYaml = null) {
  if (!dialog) return false;
  if (dialog.id === "tool-dialog") {
    if (dialog.querySelector("#tool-error")?.textContent === "Loading editor...") return false;
    if (typeof toolInitialYaml === "string") {
      return dialog.querySelector("#tool-yaml")?.value !== toolInitialYaml;
    }
  }
  return Array.isArray(baseline) && !same(dialogState(dialog), baseline);
}

export function syncGuestDirty(panel) {
  const scope = currentPageScope(panel);
  return Boolean(panel._viewKey?.() === "capabilities/guest-mode" && scope?.dirty());
}

const EDITOR_IDS = ["rule-dialog", "tool-dialog", "group-dialog", "knowledge-dialog", "memory-dialog", "temporary-memory-dialog"];

export function openDialogBaseline(panel, dialog) {
  if (!dialog?.id || !EDITOR_IDS.includes(dialog.id)) return;
  panel._eocDialogBaselines ||= new Map();
  if (dialog.id === "tool-dialog") {
    // Function Tools already keep their authoritative initial YAML on the panel,
    // but it arrives asynchronously after the dialog opens.
    panel._eocDialogBaselines.set(dialog.id, null);
    return;
  }
  panel._eocDialogBaselines.set(dialog.id, dialogState(dialog));
}

function clearDialogBaseline(panel, dialog) {
  if (!dialog?.id) return;
  panel._eocDialogBaselines?.delete(dialog.id);
}

function dialogDirty(panel, dialog) {
  if (!dialog?.open) return false;
  if (dialog.id === "knowledge-dialog" || dialog.id === "memory-dialog") {
    const read = dialog.id === "knowledge-dialog" ? panel._knowledgeValues : panel._memoryValues;
    return panel._editorInitial != null && !same(read.call(panel), panel._editorInitial);
  }
  if (dialog.id === "temporary-memory-dialog") return getRouteFeature("data-memory/memories")?.temporaryMemoryDirty(panel) || false;
  return dialogHasUnsavedChanges(
    dialog,
    panel._eocDialogBaselines?.get(dialog.id),
    dialog.id === "tool-dialog" ? panel._toolInitialYaml : null,
  );
}

function modifiedOpenDialog(panel) {
  for (const id of EDITOR_IDS) {
    const dialog = panel.shadowRoot?.querySelector?.(`#${id}`);
    if (dialogDirty(panel, dialog)) return dialog;
  }
  return null;
}

export async function confirmDialogClose(panel, dialog) {
  if (panel._eocDialogClosePending) return false;
  panel._eocDialogClosePending = true;
  try { return await closeEditor(panel, dialog); }
  finally { panel._eocDialogClosePending = false; }
}

async function closeEditor(panel, dialog) {
  if (!dialog?.open) return true;
  if (!dialogDirty(panel, dialog)) {
    clearDialogBaseline(panel, dialog);
    dialog.close();
    return true;
  }
  const discard = await panel._confirm(
    "Discard unsaved changes?",
    "Your changes in this editor have not been saved.",
    "Discard",
  );
  if (!discard) return false;
  clearDialogBaseline(panel, dialog);
  dialog.close();
  return true;
}

function ensureWindowGuards(panel) {
  if (!panel._eocStateBeforeUnload) {
    panel._eocStateBeforeUnload = (event) => {
      if (!pageCoordinator(panel).hasChanges()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", panel._eocStateBeforeUnload);
  }
  if (!panel._eocStateFocus) {
    panel._eocStateFocus = () => {
      const view = panel._viewKey();
      const key = panel._sectionCacheKey?.(view);
      const loadedAt = key ? panel._eocSectionCacheTimes?.get(key) : null;
      if (pageCoordinator(panel).hasChanges() || modifiedOpenDialog(panel)) return;
      if (loadedAt && Date.now() - loadedAt > SECTION_CACHE_TTL_MS) void panel._loadSection(true);
    };
    window.addEventListener("focus", panel._eocStateFocus);
  }
}

export function bindStateSafety(panel) {
  pageCoordinator(panel).register("editors", {
    dirty: () => Boolean(modifiedOpenDialog(panel)),
    discard: () => {
      for (const id of EDITOR_IDS) {
        const dialog = panel.shadowRoot?.querySelector?.(`#${id}`);
        if (dialog?.open) { clearDialogBaseline(panel, dialog); dialog.close(); }
      }
    },
  });
  ensureWindowGuards(panel);
  const root = panel.shadowRoot;
  if (!root || root.__eocStateSafetyBound) return;
  root.__eocStateSafetyBound = true;

  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    if (!button) return;
    const configKeys = configKeysForButton(button);
    if (configKeys.length) {
      queueMicrotask(() => applyTargetedConfigDirty(panel, configKeys, button));
    }

    // Explicit Cancel remains an intentional discard. Protect close/X buttons.
    const dialog = button.closest?.("dialog");
    if (!button.closest?.(".dialog-header") || !button.classList.contains("icon") || !EDITOR_IDS.includes(dialog?.id)) return;
    if (!dialogDirty(panel, dialog)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void confirmDialogClose(panel, dialog);
  }, true);

  root.addEventListener("cancel", (event) => {
    const dialog = event.target;
    if (!EDITOR_IDS.includes(dialog?.id)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void confirmDialogClose(panel, dialog);
  }, true);

  root.addEventListener("close", (event) => {
    clearDialogBaseline(panel, event.target);
    if (panel._eocDeferredEditorRender) queueMicrotask(() => panel._render());
  }, true);

}

export async function confirmStateSafeNavigation(panel, destination) {
  if (panel._eocUnsavedNavigationPending || panel._eocDialogClosePending) return false;
  panel._eocUnsavedNavigationPending = true;
  try { return await navigateWithUnsavedState(panel, destination); }
  finally { panel._eocUnsavedNavigationPending = false; }
}

async function navigateWithUnsavedState(panel, destination) {
  const scopes = pageCoordinator(panel).leaving(destination);
  if (scopes.some((scope) => scope.pending) || panel._eocAgentMutations) return false;
  const dialog = modifiedOpenDialog(panel);
  if (scopes.length || dialog) {
    const discard = await panel._confirm("Discard unsaved changes?", "Your changes have not been saved.", "Discard changes");
    if (!discard) return false;
    for (const scope of scopes) scope.discard();
  }
  // Close even clean editors before switching their owning context.
  for (const id of EDITOR_IDS) {
    const editor = panel.shadowRoot?.querySelector?.(`#${id}`);
    if (editor?.open) { clearDialogBaseline(panel, editor); editor.close(); }
  }
  return true;
}

export function cleanupStateSafety(panel) {
  if (panel._eocStateBeforeUnload) {
    window.removeEventListener("beforeunload", panel._eocStateBeforeUnload);
    panel._eocStateBeforeUnload = null;
  }
  if (panel._eocStateFocus) {
    window.removeEventListener("focus", panel._eocStateFocus);
    panel._eocStateFocus = null;
  }
}
