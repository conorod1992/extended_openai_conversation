const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai.management-function-repair");

function repairIssue(panel) {
  const issue = panel._selectedAgent?.()?.configuration_issue;
  return issue?.field === "functions" && issue?.repairable === true ? issue : null;
}

function functionRepairView(panel) {
  return panel._viewKey?.() === "capabilities/functions";
}

function escapeHtml(panel, value) {
  if (typeof panel._e === "function") return panel._e(String(value ?? ""));
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function editableToolsText(tools) {
  if (typeof tools === "string") return tools;
  return JSON.stringify(Array.isArray(tools) ? tools : [], null, 2);
}

function editableToolText(tool) {
  return JSON.stringify(tool ?? {}, null, 2);
}

async function loadFunctionRepair(panel, silent = false) {
  const issue = repairIssue(panel);
  if (!issue || !functionRepairView(panel)) return false;
  const loadToken = ++panel._loadToken;
  if (!silent) {
    panel._busy = true;
    panel._render();
  }
  try {
    let result;
    if (panel._data?.is_admin !== true) {
      result = {
        tools: [],
        invalid_tools: [],
        validation_error: issue.message,
        administrator_required: true,
      };
    } else {
      result = await panel._call("function_repair", "get");
    }
    if (loadToken !== panel._loadToken) return true;
    panel._contentData = null;
    panel._result = {...result, function_repair: true};
    panel._error = null;
  } catch (err) {
    if (loadToken === panel._loadToken) panel._error = err.message || String(err);
  } finally {
    if (loadToken === panel._loadToken) {
      panel._busy = false;
      panel._render();
    }
  }
  return true;
}

function invalidToolEditors(panel, invalidTools) {
  return invalidTools.map((item, order) => {
    const label = item.name || `Function Tool ${Number(item.index) + 1}`;
    const error = item.validation_error || "This Function Tool is invalid.";
    return `
      <article class="function-repair-item">
        <h3>${escapeHtml(panel, label)}</h3>
        <div class="error" role="alert">${escapeHtml(panel, error)}</div>
        <label class="field">
          <span>Invalid Function Tool</span>
          <textarea class="function-repair-item-editor" data-repair-order="${order}" data-repair-index="${escapeHtml(panel, item.index)}" rows="14" spellcheck="false">${escapeHtml(panel, editableToolText(item.tool))}</textarea>
        </label>
      </article>`;
  }).join("");
}

function renderFunctionRepair(panel, issue) {
  const result = panel._result || {};
  const validationError = result.validation_error || issue.message || "The saved Function Tools are invalid.";
  if (result.administrator_required || panel._data?.is_admin !== true) {
    return `
      <section class="card function-repair" aria-labelledby="function-repair-title">
        <h2 id="function-repair-title">Function Tools need repair</h2>
        <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
        <p>This conversation agent is still configured, but one or more saved Function Tools no longer pass the current validation rules.</p>
        <p>Administrator permission is required to repair this configuration.</p>
      </section>`;
  }

  const invalidTools = Array.isArray(result.invalid_tools) ? result.invalid_tools : [];
  const isolated = invalidTools.length > 0;
  return `
    <section class="card function-repair" aria-labelledby="function-repair-title">
      <h2 id="function-repair-title">Function Tools need repair</h2>
      <p>${isolated
        ? `${invalidTools.length} invalid Function Tool${invalidTools.length === 1 ? " is" : "s are"} shown below. Valid Function Tools are left untouched, and other assistant settings remain editable.`
        : "The saved Function Tools cannot be isolated individually, so the complete saved value is shown for repair. Other assistant settings remain editable."}</p>
      ${isolated ? invalidToolEditors(panel, invalidTools) : `
        <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
        <label class="field">
          <span>Saved Function Tools</span>
          <textarea id="function-repair-editor" rows="20" spellcheck="false" aria-describedby="function-repair-help">${escapeHtml(panel, editableToolsText(result.tools))}</textarea>
        </label>`}
      <p id="function-repair-help" class="muted">Saving validates the complete Function Tool collection with the current rules and changes only the Function Tools field; unrelated agent settings are preserved.</p>
      <div class="actions">
        <button id="function-repair-save" type="button">Validate and save repair</button>
      </div>
    </section>`;
}

function repairedToolsFromEditors(panel) {
  const result = panel._result || {};
  const invalidTools = Array.isArray(result.invalid_tools) ? result.invalid_tools : [];
  if (!invalidTools.length) {
    const editor = panel.shadowRoot?.querySelector?.("#function-repair-editor");
    const tools = JSON.parse(editor?.value || "");
    if (!Array.isArray(tools)) throw new Error("Function Tools must be a JSON array");
    return tools;
  }

  if (!Array.isArray(result.tools)) {
    throw new Error("Saved Function Tools are not an array and cannot be repaired individually");
  }
  const tools = JSON.parse(JSON.stringify(result.tools));
  panel.shadowRoot?.querySelectorAll?.(".function-repair-item-editor").forEach((editor) => {
    const index = Number(editor.dataset.repairIndex);
    if (!Number.isInteger(index) || index < 0 || index >= tools.length) {
      throw new Error("An invalid Function Tool changed position; reload and try again");
    }
    tools[index] = JSON.parse(editor.value || "");
  });
  return tools;
}

function bindFunctionRepair(panel) {
  const button = panel.shadowRoot?.querySelector?.("#function-repair-save");
  if (!button || button.dataset.eocBound === "true") return;
  button.dataset.eocBound = "true";
  button.addEventListener("click", () => {
    void (async () => {
      let tools;
      try {
        tools = repairedToolsFromEditors(panel);
      } catch (err) {
        panel._toast(`Function Tools must be valid JSON: ${err.message || String(err)}`, true);
        return;
      }
      if (typeof panel._setSaving === "function") panel._setSaving(button, true);
      else button.disabled = true;
      try {
        await panel._call("function_repair", "save", {
          tools,
          revision: panel._result?.revision,
        });
        const selectedId = panel._agentId;
        await panel._loadAgents(selectedId);
        panel._clearConfigDraft?.();
        await panel._loadSection?.();
        panel._toast("Function Tools repaired");
      } catch (err) {
        panel._toast(`Unable to repair Function Tools: ${err.message || String(err)}`, true);
      } finally {
        if (typeof panel._setSaving === "function") panel._setSaving(button, false);
        else button.disabled = false;
      }
    })();
  });
}

function install() {
  const Panel = customElements.get(PANEL_TAG);
  if (!Panel || Panel.prototype[PATCHED]) return false;
  const prototype = Panel.prototype;
  prototype[PATCHED] = true;

  const originalCall = prototype._call;
  prototype._call = function(section, action, data = {}) {
    if (repairIssue(this) && section === "configuration") {
      const repairAction = {
        get: "configuration_get",
        validate: "configuration_validate",
        save: "configuration_save",
        update: "configuration_save",
      }[action];
      if (repairAction) {
        return originalCall.call(this, "function_repair", repairAction, data);
      }
    }
    return originalCall.call(this, section, action, data);
  };

  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    if (repairIssue(this) && functionRepairView(this)) {
      return loadFunctionRepair(this, silent);
    }
    return originalLoadSection.call(this, silent);
  };

  const originalContent = prototype._content;
  prototype._content = function(agent) {
    const issue = agent?.configuration_issue;
    if (
      functionRepairView(this)
      && issue?.field === "functions"
      && issue?.repairable === true
    ) {
      return renderFunctionRepair(this, issue);
    }
    return originalContent.call(this, agent);
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    if (repairIssue(this) && functionRepairView(this)) bindFunctionRepair(this);
    return result;
  };
  return true;
}

if (typeof customElements !== "undefined") {
  customElements.whenDefined(PANEL_TAG).then(install);
}

export {
  bindFunctionRepair,
  editableToolsText,
  functionRepairView,
  loadFunctionRepair,
  renderFunctionRepair,
  repairIssue,
  repairedToolsFromEditors,
};
