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

function repairMetadata(panel) {
  return panel._result?.function_repair || null;
}

function invalidToolGroups(repair, item) {
  const groups = Array.isArray(repair?.persisted_groups) ? repair.persisted_groups : [];
  return groups
    .filter((group) => Array.isArray(group?.functions) && item?.name && group.functions.includes(item.name))
    .map((group) => group.name || group.id)
    .filter(Boolean);
}

function invalidToolCards(panel, repair) {
  const invalidTools = Array.isArray(repair?.invalid_tools) ? repair.invalid_tools : [];
  if (!invalidTools.length) return "";
  return `<article class="function-group-card function-repair-attention" data-group-search="needs attention invalid quarantined repair">
    <div class="function-group-heading"><div><div class="tool-title"><h3>Needs attention</h3><span class="availability-badge">${invalidTools.length} ${invalidTools.length === 1 ? "function" : "functions"}</span></div><p>These Function Tools are temporarily unavailable. Other valid functions and groups continue to work normally.</p></div></div>
    <details open><summary>Show functions needing repair</summary><div class="list tool-list">
      ${invalidTools.map((item) => {
        const label = item.name || `Function Tool ${Number(item.index) + 1}`;
        const groups = invalidToolGroups(repair, item);
        const groupText = groups.length ? `Assigned to: ${groups.join(", ")}. Assignment is retained while this tool is unavailable.` : "Not currently assigned to a Function Group.";
        return `<article class="list-card tool-card tool-card-invalid" data-repair-index="${escapeHtml(panel, item.index)}" data-tool-search="${escapeHtml(panel, `${label} invalid needs repair ${item.validation_error || ""}`.toLowerCase())}">
          <div class="tool-card-main"><div class="tool-title"><h3>${escapeHtml(panel, label)}</h3><span class="status-pill status-off">Needs repair</span></div><p>${escapeHtml(panel, item.validation_error || "This Function Tool does not pass the current validation rules.")}</p><small>${escapeHtml(panel, groupText)}</small></div>
          <div class="actions tool-card-actions"><button type="button" class="secondary edit-invalid-tool" data-repair-index="${escapeHtml(panel, item.index)}">Edit</button><button type="button" class="danger delete-invalid-tool" data-repair-index="${escapeHtml(panel, item.index)}">Delete</button></div>
        </article>`;
      }).join("")}
    </div></details>
  </article>`;
}

function renderFallbackRepair(panel, issue) {
  const result = panel._result || {};
  const repair = repairMetadata(panel) || {};
  const validationError = repair.validation_error || result.validation_error || issue.message || "The saved Function Tools are invalid.";
  return `<section class="card function-repair" aria-labelledby="function-repair-title">
    <h2 id="function-repair-title">Function Tools need repair</h2>
    <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
    <p>The saved Function Tool collection cannot be separated safely into valid and invalid entries. This fallback editor is only used for collection-level corruption.</p>
    <label class="field"><span>Saved Function Tools</span><textarea id="function-repair-editor" rows="20" spellcheck="false">${escapeHtml(panel, editableToolsText(result.tools))}</textarea></label>
    <div class="actions"><button id="function-repair-save" type="button">Validate and save repair</button></div>
  </section>`;
}

function decorateFunctionsContent(panel, html) {
  const repair = repairMetadata(panel);
  if (!repair?.isolatable || !Array.isArray(repair.invalid_tools) || !repair.invalid_tools.length) return html;
  const cards = invalidToolCards(panel, repair);
  const marker = '<div class="function-groups">';
  return html.includes(marker) ? html.replace(marker, `${marker}${cards}`) : `${cards}${html}`;
}

function openInvalidToolEditor(panel, item) {
  const root = panel.shadowRoot;
  const dialog = root?.querySelector("#tool-dialog");
  const editor = root?.querySelector("#tool-yaml");
  const status = root?.querySelector("#tool-error");
  if (!dialog || !editor) return;
  panel._repairToolIndex = Number(item.index);
  panel._repairToolRevision = panel._result?.revision;
  panel._toolOriginalName = null;
  editor.value = item.yaml || JSON.stringify(item.tool || {}, null, 2);
  if (status) {
    status.className = "validation invalid";
    status.textContent = item.validation_error || "This Function Tool needs repair.";
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.open = true;
  editor.focus?.();
}

async function refreshAfterRepair(panel, message) {
  const selectedId = panel._agentId;
  await panel._loadAgents(selectedId);
  panel._clearConfigDraft?.();
  await panel._loadSection?.();
  panel._toast(message);
}

async function saveInvalidTool(panel, button) {
  const index = panel._repairToolIndex;
  if (!Number.isInteger(index)) return false;
  const editor = panel.shadowRoot?.querySelector("#tool-yaml");
  const yaml = editor?.value || "";
  if (typeof panel._setSaving === "function") panel._setSaving(button, true);
  else button.disabled = true;
  try {
    const validation = await panel._call("tools", "validate_yaml", {yaml});
    if (!validation?.valid) {
      const detail = Object.values(validation?.errors || {})[0] || "Function Tool is still invalid.";
      const status = panel.shadowRoot?.querySelector("#tool-error");
      if (status) {
        status.className = "validation invalid";
        status.textContent = detail;
      }
      return true;
    }
    await panel._call("function_repair", "save_one", {
      index,
      tool: validation.config,
      revision: panel._repairToolRevision,
    });
    panel.shadowRoot?.querySelector("#tool-dialog")?.close?.();
    panel._repairToolIndex = null;
    await refreshAfterRepair(panel, "Function Tool repaired");
  } catch (err) {
    panel._toast(`Unable to repair Function Tool: ${err.message || String(err)}`, true);
  } finally {
    if (typeof panel._setSaving === "function") panel._setSaving(button, false);
    else button.disabled = false;
  }
  return true;
}

async function deleteInvalidTool(panel, item, button) {
  const label = item.name || `Function Tool ${Number(item.index) + 1}`;
  const confirmed = typeof panel._confirm === "function"
    ? await panel._confirm(`Delete ${label}?`, "Its retained Function Group assignment will also be removed.")
    : globalThis.confirm?.(`Delete ${label}?`) !== false;
  if (!confirmed) return;
  button.disabled = true;
  try {
    await panel._call("function_repair", "delete_one", {
      index: Number(item.index),
      revision: panel._result?.revision,
    });
    await refreshAfterRepair(panel, "Function Tool deleted");
  } catch (err) {
    panel._toast(`Unable to delete Function Tool: ${err.message || String(err)}`, true);
    button.disabled = false;
  }
}

function bindIsolatedRepair(panel) {
  if (!functionRepairView(panel)) return;
  const repair = repairMetadata(panel);
  const invalidTools = Array.isArray(repair?.invalid_tools) ? repair.invalid_tools : [];
  const byIndex = new Map(invalidTools.map((item) => [Number(item.index), item]));
  const root = panel.shadowRoot;
  root?.querySelectorAll?.(".edit-invalid-tool").forEach((button) => {
    if (button.dataset.eocBound === "true") return;
    button.dataset.eocBound = "true";
    button.addEventListener("click", () => {
      const item = byIndex.get(Number(button.dataset.repairIndex));
      if (item) openInvalidToolEditor(panel, item);
    });
  });
  root?.querySelectorAll?.(".delete-invalid-tool").forEach((button) => {
    if (button.dataset.eocBound === "true") return;
    button.dataset.eocBound = "true";
    button.addEventListener("click", () => {
      const item = byIndex.get(Number(button.dataset.repairIndex));
      if (item) void deleteInvalidTool(panel, item, button);
    });
  });
  const save = root?.querySelector?.("#tool-save");
  if (save && save.dataset.eocRepairCapture !== "true") {
    save.dataset.eocRepairCapture = "true";
    save.addEventListener("click", (event) => {
      if (!Number.isInteger(panel._repairToolIndex)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      void saveInvalidTool(panel, save);
    }, true);
  }
  const dialog = root?.querySelector?.("#tool-dialog");
  if (dialog && dialog.dataset.eocRepairClose !== "true") {
    dialog.dataset.eocRepairClose = "true";
    dialog.addEventListener("close", () => { panel._repairToolIndex = null; });
  }
}

function bindFallbackRepair(panel) {
  const button = panel.shadowRoot?.querySelector?.("#function-repair-save");
  if (!button || button.dataset.eocBound === "true") return;
  button.dataset.eocBound = "true";
  button.addEventListener("click", () => {
    void (async () => {
      let tools;
      try {
        tools = JSON.parse(panel.shadowRoot?.querySelector?.("#function-repair-editor")?.value || "");
        if (!Array.isArray(tools)) throw new Error("Function Tools must be a JSON array");
      } catch (err) {
        panel._toast(`Function Tools must be valid JSON: ${err.message || String(err)}`, true);
        return;
      }
      button.disabled = true;
      try {
        await panel._call("function_repair", "save", {tools, revision: panel._result?.revision});
        await refreshAfterRepair(panel, "Function Tools repaired");
      } catch (err) {
        panel._toast(`Unable to repair Function Tools: ${err.message || String(err)}`, true);
      } finally {
        button.disabled = false;
      }
    })();
  });
}

export function installFunctionRepair(Panel) {
  if (!Panel || Panel.prototype[PATCHED]) return false;
  const prototype = Panel.prototype;
  prototype[PATCHED] = true;

  const originalContent = prototype._content;
  prototype._content = function(agent) {
    const issue = agent?.configuration_issue;
    if (functionRepairView(this) && issue?.field === "functions" && issue?.repairable === true) {
      const repair = repairMetadata(this);
      if (repair && repair.isolatable === false) return renderFallbackRepair(this, issue);
      return decorateFunctionsContent(this, originalContent.call(this, agent));
    }
    return originalContent.call(this, agent);
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    if (repairIssue(this) && functionRepairView(this)) {
      if (repairMetadata(this)?.isolatable === false) bindFallbackRepair(this);
      else bindIsolatedRepair(this);
    }
    return result;
  };
  return true;
}


export {
  bindFallbackRepair,
  bindIsolatedRepair,
  decorateFunctionsContent,
  editableToolsText,
  functionRepairView,
  invalidToolCards,
  repairIssue,
  repairMetadata,
  renderFallbackRepair,
};
