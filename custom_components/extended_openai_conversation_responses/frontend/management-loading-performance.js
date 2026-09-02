import {ensureGuideModule} from "./guide-page.js";
import {ensureOverviewModule} from "./overview-page.js";

const PATCHED = Symbol.for("extended-openai.management-loading-performance");
const SCOPE_CACHE_TTL_MS = 30_000;

const clone = (value) => JSON.parse(JSON.stringify(value));

function viewAssetPromise(view) {
  if (view === "overview") return ensureOverviewModule();
  if (view === "guide") return ensureGuideModule();
  if (view === "usage-maintenance/request-debug") return import("./debug-panel.js");
  return null;
}

function fieldErrorKey(key) {
  return key === "title" ? "__title" : key;
}

function showErrors(panel, errors = {}) {
  const root = panel.shadowRoot;
  root.querySelectorAll(".field-error").forEach((item) => { item.textContent = ""; });
  Object.entries(errors).forEach(([key, message]) => {
    const mappedKey = fieldErrorKey(key);
    const escaped = CSS.escape(mappedKey);
    const fallback = CSS.escape(fieldErrorKey(key.split("[")[0]));
    const target = root.querySelector(`[data-error="${escaped}"]`) || root.querySelector(`[data-error="${fallback}"]`);
    if (target) target.textContent = message;
  });
}

function normalizeGuestModeTimestamp(value) {
  if (typeof value !== "string" || !value) return value;
  if (/(?:z|[+-]\d{2}:\d{2})$/i.test(value)) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

function validatedImportMatches(validatedDocument, currentDocument) {
  return typeof validatedDocument === "string" && validatedDocument === currentDocument;
}

function setControlPending(panel, control, pending) {
  if (!control) return;
  if (control.tagName === "BUTTON" && typeof panel._setSaving === "function") {
    panel._setSaving(control, pending);
    return;
  }
  control.disabled = pending;
}

async function runFrontendMutation(panel, control, label, operation) {
  if (!control || control.dataset?.eocMutationPending === "true") return false;
  control.dataset.eocMutationPending = "true";
  setControlPending(panel, control, true);
  try {
    await operation();
    return true;
  } catch (err) {
    panel._toast(`Unable to ${label}: ${err.message || String(err)}`, true);
    return false;
  } finally {
    delete control.dataset.eocMutationPending;
    setControlPending(panel, control, false);
  }
}

async function saveConfiguration(panel, button) {
  if (!panel._draft || !panel._selectedAgent?.()) return;
  panel._setSaving(button, true);
  try {
    const result = await panel._call("configuration", "save", {
      config: clone(panel._draft),
      title: panel._draftTitle,
    });
    showErrors(panel, result.errors || {});
    if (!result.valid) {
      panel._toast("Fix the highlighted configuration errors", true);
      return;
    }

    const {valid: _valid, errors: _errors, agent, ...saved} = result;
    panel._configData = {...panel._configData, ...saved};
    panel._result = panel._configData;
    panel._draft = clone(saved.config);
    panel._draftTitle = saved.title;
    panel._draftAgentId = panel._agentId;
    if (agent) Object.assign(panel._selectedAgent(), agent);
    panel._setConfigDirty(false);
    panel._toast("Configuration saved");
    panel._render();
  } catch (err) {
    panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
  } finally {
    panel._setSaving(button, false);
  }
}

function bindSingleRequestSave(panel) {
  const root = panel.shadowRoot;
  if (root.__eocSingleRequestSaveBound) return;
  root.__eocSingleRequestSaveBound = true;
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("#save-config");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void saveConfiguration(panel, button);
  }, true);
}

function ruleSensitivityValue(value) {
  return value === "Conservative" ? 94 : value === "Tolerant" ? 84 : 90;
}

function bindFrontendCorrectness(panel) {
  const root = panel.shadowRoot;
  if (root.__eocFrontendCorrectnessBound) return;
  root.__eocFrontendCorrectnessBound = true;

  root.addEventListener("input", (event) => {
    const input = event.target;
    if (input?.id !== "import-document") return;
    const hadPreview = typeof panel._importDocument === "string";
    panel._importDocument = null;
    const apply = root.querySelector("#import-apply");
    if (apply) apply.disabled = true;
    if (hadPreview) {
      const summary = root.querySelector("#import-summary");
      if (summary) summary.textContent = "Document changed. Validate & preview again before importing.";
    }
  }, true);

  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    if (!button) return;

    if (button.id === "import-preview") {
      panel._importDocument = null;
      const apply = root.querySelector("#import-apply");
      if (apply) apply.disabled = true;
      return;
    }

    if (button.id === "import-apply") {
      const current = root.querySelector("#import-document")?.value ?? "";
      if (validatedImportMatches(panel._importDocument, current)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      button.disabled = true;
      panel._toast("Validate & preview the current import document before importing it", true);
      return;
    }

    if (button.id === "guest-policy-save") {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        const saved = await runFrontendMutation(panel, button, "save Guest policy", async () => {
          await panel._call("guest_mode", "save_policy", {config: clone(panel._guestDraft || {})});
        });
        if (!saved) return;
        await panel._loadSection(true);
        panel._toast("Guest policy saved");
      })();
      return;
    }

    if (button.classList.contains("rule-duplicate")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        const saved = await runFrontendMutation(panel, button, "duplicate Request Rule", () =>
          panel._call("request_rules", "duplicate", {rule_id: button.dataset.id})
        );
        if (saved) await panel._loadSection();
      })();
      return;
    }

    if (button.classList.contains("rule-delete")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        if (!await panel._confirm("Delete Request Rule?", "This cannot be undone.", "Delete")) return;
        const saved = await runFrontendMutation(panel, button, "delete Request Rule", () =>
          panel._call("request_rules", "delete", {rule_id: button.dataset.id, confirm: true})
        );
        if (saved) await panel._loadSection();
      })();
      return;
    }

    if (button.id === "rules-default-save") {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        const saved = await runFrontendMutation(panel, button, "save Request Rule defaults", () =>
          panel._call("request_rules", "defaults", {
            defaults: {
              word_forms: root.querySelector("#rules-default-word-forms").checked,
              wording_alternatives: root.querySelector("#rules-default-wording").checked,
              fuzzy: root.querySelector("#rules-default-fuzzy").checked,
              fuzzy_threshold: ruleSensitivityValue(root.querySelector("#rules-default-threshold").value),
            },
          })
        );
        if (saved) await panel._loadSection();
      })();
      return;
    }

    if (button.id === "wording-save") {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        const wordingGroups = [...root.querySelectorAll(".wording-group")].map((row) => ({
          canonical: row.querySelector(".wording-canonical").value.trim(),
          alternatives: row.querySelector(".wording-alternatives").value
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
        }));
        const saved = await runFrontendMutation(panel, button, "save wording alternatives", () =>
          panel._call("request_rules", "wording_groups", {wording_groups: wordingGroups})
        );
        if (saved) await panel._loadSection();
      })();
    }
  }, true);

  root.addEventListener("change", (event) => {
    const input = event.target;
    if (!input?.classList?.contains("rule-enabled")) return;
    event.stopImmediatePropagation();
    const previous = !input.checked;
    void (async () => {
      const rule = (panel._result?.rules || []).find((item) => item.id === input.dataset.id);
      if (!rule) {
        input.checked = previous;
        return;
      }
      const saved = await runFrontendMutation(panel, input, "update Request Rule", () =>
        panel._call("request_rules", "update", {
          rule_id: rule.id,
          rule: {...rule, enabled: input.checked, sensitive_matching_warning: undefined},
        })
      );
      if (!saved) {
        input.checked = previous;
        return;
      }
      await panel._loadSection(true);
    })();
  }, true);

  root.addEventListener("submit", (event) => {
    if (event.target?.id !== "rule-form" || !panel._eocRuleSavePromise) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
}

async function loadOverview(panel, silent = false) {
  const agent = panel._selectedAgent();
  if (!agent) return panel._render();
  const loadToken = ++panel._loadToken;
  if (!silent) {
    panel._busy = true;
    panel._render();
  }
  try {
    const result = await panel._call("overview", "summary");
    if (loadToken !== panel._loadToken) return;
    if (result.agent) Object.assign(agent, result.agent);
    panel._contentData = null;
    panel._result = {
      usage: result.usage || {},
      conversations: result.conversations || {},
      load_errors: result.load_errors || [],
    };
    panel._error = null;
  } catch (err) {
    if (loadToken === panel._loadToken) panel._error = err.message || String(err);
  } finally {
    if (loadToken === panel._loadToken) {
      panel._busy = false;
      panel._render();
    }
  }
}

function isCurrentLazyLoad(panel, view, loadToken) {
  return panel._viewKey() === view && panel._loadToken === loadToken;
}

function loadSectionAfterAsset(
  panel,
  silent,
  originalLoadSection,
  view,
  assetPromise,
  loadToken,
) {
  return assetPromise
    .then(() => {
      if (!isCurrentLazyLoad(panel, view, loadToken)) return undefined;
      if (view === "overview") return loadOverview(panel, silent);
      return originalLoadSection.call(panel, silent);
    })
    .catch((err) => {
      if (!isCurrentLazyLoad(panel, view, loadToken)) return undefined;
      panel._busy = false;
      panel._error = `Unable to load this frontend section: ${err.message || String(err)}`;
      panel._render();
      return undefined;
    });
}

function install() {
  const Panel = customElements.get("extended-openai-management-panel");
  if (!Panel || Panel.prototype[PATCHED]) return;
  const prototype = Panel.prototype;
  prototype[PATCHED] = true;

  const originalCall = prototype._call;
  prototype._call = function(section, action, extra = {}) {
    let payload = extra;
    if (section === "guest_mode" && action === "update") {
      payload = {...extra};
      for (const key of ["active_from", "active_until"]) {
        if (payload[key]) payload[key] = normalizeGuestModeTimestamp(payload[key]);
      }
    }

    const ruleDialog = this.shadowRoot?.querySelector?.("#rule-dialog");
    const ruleSave = section === "request_rules"
      && ["create", "update"].includes(action)
      && ruleDialog?.open;
    if (!ruleSave) return originalCall.call(this, section, action, payload);
    if (this._eocRuleSavePromise) return this._eocRuleSavePromise;

    const button = this.shadowRoot.querySelector("#rule-save");
    setControlPending(this, button, true);
    const request = Promise.resolve().then(() => originalCall.call(this, section, action, payload));
    const tracked = request.finally(() => {
      setControlPending(this, button, false);
      if (this._eocRuleSavePromise === tracked) this._eocRuleSavePromise = null;
    });
    this._eocRuleSavePromise = tracked;
    return tracked;
  };

  const originalCanAccessView = prototype._canAccessView;
  prototype._canAccessView = function(page, subsection = null) {
    if (page === "usage-maintenance" && subsection === "request-debug") {
      return this._data?.is_admin === true;
    }
    return originalCanAccessView.call(this, page, subsection);
  };

  const originalPrepareScopeCatalogVisit = prototype._prepareScopeCatalogVisit;
  prototype._prepareScopeCatalogVisit = function(view) {
    const key = this._scopeCatalogKey(view);
    this._scopeCatalogVisitKey = key;
    return key;
  };

  const originalLoadScopes = prototype._loadScopes;
  prototype._loadScopes = async function(scopeCatalogKey) {
    this._eocScopeCatalogTimes ||= new Map();
    const loadedAt = this._eocScopeCatalogTimes.get(scopeCatalogKey);
    if (
      scopeCatalogKey
      && this._scopeCatalogCache.has(scopeCatalogKey)
      && (!loadedAt || Date.now() - loadedAt > SCOPE_CACHE_TTL_MS)
    ) {
      this._scopeCatalogCache.delete(scopeCatalogKey);
      this._eocScopeCatalogTimes.delete(scopeCatalogKey);
    }
    await originalLoadScopes.call(this, scopeCatalogKey);
    if (scopeCatalogKey && this._scopeCatalogCache.has(scopeCatalogKey)) {
      this._eocScopeCatalogTimes.set(scopeCatalogKey, Date.now());
    }
  };

  const originalInvalidateAfterMutation = prototype._invalidateAfterMutation;
  prototype._invalidateAfterMutation = function(...args) {
    const result = originalInvalidateAfterMutation.apply(this, args);
    if (this._eocScopeCatalogTimes) {
      for (const key of this._eocScopeCatalogTimes.keys()) {
        if (!this._scopeCatalogCache.has(key)) this._eocScopeCatalogTimes.delete(key);
      }
    }
    return result;
  };

  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    const view = this._viewKey();
    const assetPromise = viewAssetPromise(view);
    if (!assetPromise) return originalLoadSection.call(this, silent);
    const loadToken = ++this._loadToken;
    return loadSectionAfterAsset(
      this,
      silent,
      originalLoadSection,
      view,
      assetPromise,
      loadToken,
    );
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    bindSingleRequestSave(this);
    bindFrontendCorrectness(this);
    return result;
  };

  // Retained only to document that the old implementation deliberately cleared
  // scope caches on every page transition. The replacement above keeps isolated
  // per-agent/per-view entries for a short TTL instead.
  void originalPrepareScopeCatalogVisit;
}

if (typeof customElements !== "undefined") {
  customElements.whenDefined("extended-openai-management-panel").then(install);
}

export {
  SCOPE_CACHE_TTL_MS,
  fieldErrorKey,
  loadSectionAfterAsset,
  normalizeGuestModeTimestamp,
  runFrontendMutation,
  validatedImportMatches,
};
