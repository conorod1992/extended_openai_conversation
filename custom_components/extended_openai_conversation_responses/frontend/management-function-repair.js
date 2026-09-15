const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai.management-function-repair");

function repairIssue(panel) {
  const issue = panel._selectedAgent?.()?.configuration_issue;
  return issue?.field === "functions" && issue?.repairable === true ? issue : null;
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

async function loadFunctionRepair(panel, silent = false) {
  const issue = repairIssue(panel);
  if (!issue) return false;
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

function renderFunctionRepair(panel, issue) {
  const result = panel._result || {};
  const validationError = result.validation_error || issue.message || "The saved Function Tools are invalid.";
  if (result.administrator_required || panel._data?.is_admin !== true) {
    return `
      <section class="card function-repair" aria-labelledby="function-repair-title">
        <h2 id="function-repair-title">Function Tools need repair</h2>
        <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
        <p>This conversation agent is still configured, but its saved Function Tools no longer pass the current validation rules.</p>
        <p>Administrator permission is required to repair this configuration.</p>
      </section>`;
  }
  return `
    <section class="card function-repair" aria-labelledby="function-repair-title">
      <h2 id="function-repair-title">Function Tools need repair</h2>
      <p>This conversation agent is still configured, but its saved Function Tools no longer pass the current validation rules. Other configuration is locked until the Function Tools are repaired.</p>
      <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
      <label class="field">
        <span>Saved Function Tools</span>
        <textarea id="function-repair-editor" rows="20" spellcheck="false" aria-describedby="function-repair-help">${escapeHtml(panel, editableToolsText(result.tools))}</textarea>
      </label>
      <p id="function-repair-help" class="muted">Edit this as a JSON array. Saving validates the replacement with the current Function Tool rules and changes only the Function Tools field; unrelated agent settings are preserved.</p>
      <div class="actions">
        <button id="function-repair-save" type="button">Validate and save Function Tools</button>
      </div>
    </section>`;
}

function bindFunctionRepair(panel) {
  const button = panel.shadowRoot?.querySelector?.("#function-repair-save");
  if (!button || button.dataset.eocBound === "true") return;
  button.dataset.eocBound = "true";
  button.addEventListener("click", () => {
    void (async () => {
      const editor = panel.shadowRoot?.querySelector?.("#function-repair-editor");
      let tools;
      try {
        tools = JSON.parse(editor?.value || "");
      } catch (err) {
        panel._toast(`Function Tools must be valid JSON: ${err.message || String(err)}`, true);
        return;
      }
      if (!Array.isArray(tools)) {
        panel._toast("Function Tools must be a JSON array", true);
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

  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    if (repairIssue(this)) return loadFunctionRepair(this, silent);
    return originalLoadSection.call(this, silent);
  };

  const originalContent = prototype._content;
  prototype._content = function(agent) {
    const issue = agent?.configuration_issue;
    if (issue?.field === "functions" && issue?.repairable === true) {
      return renderFunctionRepair(this, issue);
    }
    return originalContent.call(this, agent);
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    if (repairIssue(this)) bindFunctionRepair(this);
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
  loadFunctionRepair,
  renderFunctionRepair,
  repairIssue,
};
