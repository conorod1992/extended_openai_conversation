const clone = (value) => JSON.parse(JSON.stringify(value));

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
  if (!panel._draft || !panel._selectedAgent?.() || panel._configurationSaving) return;
  panel._configurationSaving = true;
  panel._setSaving(button, true);
  try {
    const result = await panel._call("configuration", "save", {
      config: clone(panel._draft),
      title: panel._draftTitle,
      revision: panel._configData?.revision,
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
    panel._toast("Changes saved");
    panel._render();
  } catch (err) {
    panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
  } finally {
    panel._configurationSaving = false;
    panel._setSaving(button, false);
  }
}

export function bindSingleRequestSave(panel) {
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

export function bindFrontendCorrectness(panel) {
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

    if (button.classList.contains("rule-duplicate")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      void (async () => {
        const saved = await runFrontendMutation(panel, button, "duplicate Request Rule", () =>
          panel._call("request_rules", "duplicate", {rule_id: button.dataset.id, revision: panel._result?.revision})
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
          panel._call("request_rules", "delete", {rule_id: button.dataset.id, confirm: true, revision: panel._result?.revision})
        );
        if (saved) await panel._loadSection();
      })();
      return;
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
          revision: panel._result?.revision,
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


export {fieldErrorKey, normalizeGuestModeTimestamp, runFrontendMutation, validatedImportMatches, setControlPending, saveConfiguration};
