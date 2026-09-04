import {routePath} from "./frontend-navigation.js";

const PATCHED = Symbol.for("extended-openai.management-state-safety");
export const SECTION_CACHE_TTL_MS = 30_000;

const clone = (value) => JSON.parse(JSON.stringify(value));
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
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

export function configKeyForControl(control) {
  if (!control) return null;
  if (control.dataset?.config) return control.dataset.config;
  if (control.dataset?.memoryConfig) return control.dataset.memoryConfig;
  if (control.id === "voice-mappings") return "voice_device_mappings";
  if (control.matches?.("[data-local-intent-exclusion]")) return "local_intent_exclusions";
  if (control.matches?.(".regex-pattern,.regex-replacement")) {
    return "speech_regex_replacements";
  }
  if (control.id === "conversation-timeout-preset") {
    return "conversation_timeout_minutes";
  }
  return null;
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

function applyTargetedConfigDirty(panel, keys, control = null) {
  if (!keys.length) return;
  const wasDirty = Boolean(panel._configDirty);
  const changed = syncConfigDirtyKeys(panel, keys);
  panel._setConfigDirty(changed.size > 0);
  if (wasDirty && !panel._configDirty) {
    panel._render?.();
    restoreConfigFocus(panel, control);
  }
}

function dialogState(dialog) {
  if (!dialog) return [];
  return [...dialog.querySelectorAll("input,select,textarea")]
    .filter((control) => !["button", "submit", "reset", "file", "search"].includes(control.type))
    .map((control) => ({
      id: control.id || control.name || "",
      type: control.type || control.tagName,
      checked: "checked" in control ? Boolean(control.checked) : undefined,
      value: control.value,
    }));
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

function setGuestBaseline(panel) {
  panel._eocGuestBaseline = panel._guestDraft ? clone(panel._guestDraft) : null;
  panel._eocGuestBaselineAgent = panel._agentId || null;
  panel._guestDirty = false;
}

export function syncGuestDirty(panel) {
  if (panel._viewKey?.() !== "capabilities/guest-mode" || !panel._guestDraft) return false;
  if (!panel._eocGuestBaseline || panel._eocGuestBaselineAgent !== panel._agentId) {
    setGuestBaseline(panel);
    return false;
  }
  panel._guestDirty = !same(panel._guestDraft, panel._eocGuestBaseline);
  return panel._guestDirty;
}

function openDialogBaseline(panel, dialog) {
  if (!dialog?.id || !["rule-dialog", "tool-dialog", "group-dialog"].includes(dialog.id)) return;
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
  return dialogHasUnsavedChanges(
    dialog,
    panel._eocDialogBaselines?.get(dialog.id),
    dialog.id === "tool-dialog" ? panel._toolInitialYaml : null,
  );
}

function modifiedOpenDialog(panel) {
  for (const id of ["rule-dialog", "tool-dialog", "group-dialog"]) {
    const dialog = panel.shadowRoot?.querySelector?.(`#${id}`);
    if (dialogDirty(panel, dialog)) return dialog;
  }
  return null;
}

async function confirmDialogClose(panel, dialog) {
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

function prepareSectionCache(panel, view) {
  const key = panel._sectionCacheKey?.(view);
  if (!key) return {key: null, reused: false};
  panel._eocSectionCacheTimes ||= new Map();
  const loadedAt = panel._eocSectionCacheTimes.get(key);
  const cached = panel._sectionCache?.has(key);
  if (cached && loadedAt && Date.now() - loadedAt <= SECTION_CACHE_TTL_MS) {
    return {key, reused: true};
  }
  if (cached) panel._sectionCache.delete(key);
  panel._eocSectionCacheTimes.delete(key);
  return {key, reused: false};
}

function markSectionCache(panel, key, reused) {
  // Do not slide the TTL when a cached value is merely revisited. It should
  // still become eligible for a real refresh after 30 seconds.
  if (!key || reused || !panel._sectionCache?.has(key)) return;
  panel._eocSectionCacheTimes ||= new Map();
  panel._eocSectionCacheTimes.set(key, Date.now());
}

function ensureWindowGuards(panel) {
  if (!panel._eocStateBeforeUnload) {
    panel._eocStateBeforeUnload = (event) => {
      if (!panel._guestDirty && !modifiedOpenDialog(panel)) return;
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
      if (loadedAt && Date.now() - loadedAt > SECTION_CACHE_TTL_MS) void panel._loadSection(true);
    };
    window.addEventListener("focus", panel._eocStateFocus);
  }
}

function bindStateSafety(panel) {
  ensureWindowGuards(panel);
  const root = panel.shadowRoot;
  if (!root || root.__eocStateSafetyBound) return;
  root.__eocStateSafetyBound = true;

  const syncGuestAfterEvent = () => queueMicrotask(() => syncGuestDirty(panel));
  const syncConfigAfterControlEvent = (event) => {
    const key = configKeyForControl(event.target);
    if (key) applyTargetedConfigDirty(panel, [key], event.target);
  };
  root.addEventListener("input", syncGuestAfterEvent);
  root.addEventListener("input", syncConfigAfterControlEvent);
  root.addEventListener("change", syncGuestAfterEvent);
  root.addEventListener("change", syncConfigAfterControlEvent);
  root.addEventListener("value-changed", syncGuestAfterEvent);
  root.addEventListener("value-changed", syncConfigAfterControlEvent);

  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    if (!button) return;
    const configKeys = configKeysForButton(button);
    if (configKeys.length) {
      queueMicrotask(() => applyTargetedConfigDirty(panel, configKeys, button));
    }
    if (button.matches("#rule-add,#rule-empty-add,#add-tool,.edit-tool,.duplicate-tool,#add-group,.edit-group")) {
      requestAnimationFrame(() => {
        const dialog = root.querySelector("#rule-dialog[open],#tool-dialog[open],#group-dialog[open]");
        openDialogBaseline(panel, dialog);
      });
      return;
    }

    // Explicit Cancel remains an intentional discard. Protect close/X buttons.
    const dialog = button.closest?.("dialog");
    if (!button.classList.contains("icon") || !["rule-dialog", "tool-dialog", "group-dialog"].includes(dialog?.id)) return;
    if (!dialogDirty(panel, dialog)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void confirmDialogClose(panel, dialog);
  }, true);

  root.addEventListener("cancel", (event) => {
    const dialog = event.target;
    if (!["rule-dialog", "tool-dialog", "group-dialog"].includes(dialog?.id)) return;
    if (!dialogDirty(panel, dialog)) {
      clearDialogBaseline(panel, dialog);
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    void confirmDialogClose(panel, dialog);
  }, true);

  root.addEventListener("close", (event) => clearDialogBaseline(panel, event.target), true);

  root.addEventListener("change", (event) => {
    const select = event.target;
    if (select?.id !== "agent" || panel._viewKey() !== "capabilities/guest-mode" || !syncGuestDirty(panel)) return;
    const nextAgent = select.value;
    const currentAgent = panel._agentId;
    select.value = currentAgent;
    event.preventDefault();
    event.stopImmediatePropagation();
    void (async () => {
      const discard = await panel._confirm(
        "Discard unsaved Guest policy changes?",
        "Switching agents will discard your unsaved Guest Mode policy changes.",
        "Discard",
      );
      if (!discard) return;
      panel._guestDirty = false;
      panel._agentId = nextAgent;
      localStorage.setItem("extended-openai-agent", panel._agentId);
      panel._clearConfigDraft();
      panel._scopeId = null;
      panel._applyScopes(panel._scopeCatalogCache.get(panel._scopeCatalogKey()) || panel._baseScopes);
      panel._eocGuestBaseline = null;
      panel._eocGuestBaselineAgent = null;
      await panel._loadSection();
    })();
  }, true);
}

async function confirmStateSafeNavigation(panel, destination) {
  if (panel._viewKey() === "capabilities/guest-mode" && destination !== "capabilities/guest-mode" && syncGuestDirty(panel)) {
    const discard = await panel._confirm(
      "Discard unsaved Guest policy changes?",
      "Your Guest Mode policy changes have not been saved.",
      "Discard",
    );
    if (!discard) return false;
    panel._guestDirty = false;
    panel._eocGuestBaseline = null;
    panel._eocGuestBaselineAgent = null;
  }
  const dialog = modifiedOpenDialog(panel);
  if (dialog && !await confirmDialogClose(panel, dialog)) return false;
  return true;
}

export function installManagementStateSafety(registry = globalThis.customElements) {
  if (typeof window === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const Panel = registry.get("extended-openai-management-panel");
    const prototype = Panel?.prototype;
    if (!prototype || prototype[PATCHED]) return false;
    prototype[PATCHED] = true;

    const originalSetConfigDirty = prototype._setConfigDirty;
    prototype._setConfigDirty = function(value) {
      if (!value) {
        this._eocDirtyConfigKeys = new Set();
        return originalSetConfigDirty.call(this, false);
      }
      const result = originalSetConfigDirty.call(this, true);
      if (this._eocDirtyConfigKeys instanceof Set) {
        queueMicrotask(() => {
          if (!(this._eocDirtyConfigKeys instanceof Set) || this._eocDirtyConfigKeys.size) return;
          const changed = rebuildConfigDirtyKeys(this);
          const dirty = changed.size > 0;
          const wasDirty = Boolean(this._configDirty);
          originalSetConfigDirty.call(this, dirty);
          if (wasDirty && !dirty) this._render?.();
        });
      }
      return result;
    };

    prototype._syncConfigDirty = function() {
      const changed = rebuildConfigDirtyKeys(this);
      return originalSetConfigDirty.call(this, changed.size > 0);
    };

    const originalNavigate = prototype._navigate;
    prototype._navigate = async function(page, subsection = null) {
      const targetSubsection = subsection || this._visibleSubsections(page)[0]?.id || null;
      const destination = targetSubsection ? `${page}/${targetSubsection}` : page;
      if (!await confirmStateSafeNavigation(this, destination)) return;
      return originalNavigate.call(this, page, subsection);
    };

    const originalHandleRouteChange = prototype._handleRouteChange;
    prototype._handleRouteChange = async function(route) {
      const destination = route.section ? `${route.page}/${route.section}` : route.page;
      if (!await confirmStateSafeNavigation(this, destination)) {
        history.pushState({}, "", routePath(this._page, this._subsection));
        return;
      }
      return originalHandleRouteChange.call(this, route);
    };

    const originalStartFreshGuestPolicy = prototype._startFreshGuestPolicy;
    prototype._startFreshGuestPolicy = async function(...args) {
      const result = await originalStartFreshGuestPolicy.apply(this, args);
      syncGuestDirty(this);
      return result;
    };

    const originalSetupGuestSelectors = prototype._setupGuestSelectors;
    prototype._setupGuestSelectors = function(...args) {
      const result = originalSetupGuestSelectors.apply(this, args);
      this.shadowRoot.querySelectorAll("ha-selector[data-guest-key]").forEach((selector) => {
        if (selector.__eocGuestDirtyBound) return;
        selector.__eocGuestDirtyBound = true;
        selector.addEventListener("value-changed", () => queueMicrotask(() => syncGuestDirty(this)));
      });
      return result;
    };

    const originalLoadAgents = prototype._loadAgents;
    prototype._loadAgents = async function(selectedId = null) {
      const preserveGuestDraft = this._viewKey() === "capabilities/guest-mode"
        && (!selectedId || selectedId === this._agentId)
        && syncGuestDirty(this);
      const guestDraft = preserveGuestDraft ? clone(this._guestDraft) : null;
      const guestBaseline = preserveGuestDraft ? clone(this._eocGuestBaseline) : null;
      const guestAgent = preserveGuestDraft ? this._agentId : null;
      const result = await originalLoadAgents.call(this, selectedId);
      if (preserveGuestDraft && this._agentId === guestAgent) {
        this._guestDraft = guestDraft;
        this._eocGuestBaseline = guestBaseline;
        this._eocGuestBaselineAgent = guestAgent;
        this._guestDirty = true;
        this._render();
      }
      return result;
    };

    const originalLoadSection = prototype._loadSection;
    prototype._loadSection = function(silent = false) {
      const view = this._viewKey();
      const cache = prepareSectionCache(this, view);
      const result = originalLoadSection.call(this, silent);
      return Promise.resolve(result).then((value) => {
        markSectionCache(this, cache.key, cache.reused);
        if (view === "capabilities/guest-mode" && this._guestDraft && !this._guestDirty) setGuestBaseline(this);
        return value;
      });
    };

    const originalInvalidate = prototype._invalidateAfterMutation;
    prototype._invalidateAfterMutation = function(...args) {
      const result = originalInvalidate.apply(this, args);
      if (this._eocSectionCacheTimes) {
        for (const key of this._eocSectionCacheTimes.keys()) {
          if (!this._sectionCache.has(key)) this._eocSectionCacheTimes.delete(key);
        }
      }
      if (args[1] === "guest_mode" && args[2] === "save_policy") setGuestBaseline(this);
      return result;
    };

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      bindStateSafety(this);
      return result;
    };

    const originalDisconnected = prototype.disconnectedCallback;
    prototype.disconnectedCallback = function(...args) {
      if (this._eocStateBeforeUnload) {
        window.removeEventListener("beforeunload", this._eocStateBeforeUnload);
        this._eocStateBeforeUnload = null;
      }
      if (this._eocStateFocus) {
        window.removeEventListener("focus", this._eocStateFocus);
        this._eocStateFocus = null;
      }
      return originalDisconnected?.apply(this, args);
    };

    return true;
  });
}

if (typeof customElements !== "undefined") {
  void installManagementStateSafety();
}
