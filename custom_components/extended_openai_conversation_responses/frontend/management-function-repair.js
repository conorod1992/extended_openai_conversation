import {captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement} from "./configuration-acknowledgement.js";
import {getToolYamlEditor} from "./tool-yaml-editor-adapter.js";

function repairIssue(panel) {
  const issue = panel._selectedAgent?.()?.configuration_issue;
  if (functionRepairView(panel) && panel._result?.config) {
    // Direct Functions visits may precede the Overview health read. The loaded
    // configuration is authoritative, including when an earlier issue was fixed.
    const repair = repairMetadata(panel);
    if (!repair) return null;
    return {
      field: "functions",
      message: repair.validation_error || issue?.message || "Saved Function Tools need repair.",
      repairable: true,
    };
  }
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
  return JSON.stringify(tools ?? [], null, 2);
}

function repairMetadata(panel) {
  const repair = panel._result?.function_repair;
  if (!repair) return null;
  return repair.validation_error || repair.invalid_count > 0 || repair.invalid_tools?.length
    ? repair
    : null;
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
    <details class="eoc-details-base" open><summary>Show functions needing repair</summary><div class="list tool-list">
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
    <p>This recovery editor appears only when the saved Function Tools cannot be repaired individually.</p>
    <label class="field"><span>Saved Function Tools</span><textarea id="function-repair-editor" rows="20" spellcheck="false">${escapeHtml(panel, editableToolsText(repair.tools ?? result.tools))}</textarea></label>
    <div class="actions"><button id="function-repair-save" type="button">Validate and save repair</button></div>
  </section>`;
}

function requiresToolRepairFallback(panel) {
  const repair = repairMetadata(panel);
  return repair?.isolatable === false && repair.tools_valid !== true;
}

function renderFunctionRepairCards(panel) {
  const repair = repairMetadata(panel);
  if (!repair) return "";
  const usableIds = new Set((panel._result?.config?.function_groups || []).map(group => group.id));
  const raw = Array.isArray(repair.persisted_groups) ? repair.persisted_groups : [];
  const issues = Array.isArray(repair.group_issues) ? repair.group_issues : [];
  const groups = raw.filter((group, index) => group && typeof group.id === "string"
    && raw.filter(item => item?.id === group.id).length === 1
    && (!usableIds.has(group.id) || issues.some(issue => issue.id === group.id || issue.index === index)));
  const tools = repair.isolatable ? invalidToolCards(panel, repair) : "";
  const cards = groups.map(group => {
    const label = group.name || group.id || "Unnamed Function Group";
    return `<article class="function-group-card group-repair-entry" data-group-id="${escapeHtml(panel, group.id)}" data-group-search="needs repair group ${escapeHtml(panel, label)}">
      <div class="function-group-heading"><div><h3>${escapeHtml(panel, label)}</h3><span class="status-pill status-off">Needs repair</span><p>This saved Function Group is unavailable or has unavailable members. Edit it to save a valid definition, or delete it. Saving a repair removes unavailable members from this Group.</p></div>
      <div class="actions"><button type="button" class="secondary edit-group" data-group-id="${escapeHtml(panel, group.id)}">Edit</button><button type="button" class="danger delete-group" data-group-id="${escapeHtml(panel, group.id)}">Delete</button></div></div></article>`;
  }).join("");
  return tools || cards ? `<div class="function-repair-attention" style="display:grid;gap:18px">${tools}${cards}</div>` : "";
}

async function openInvalidToolEditor(panel, item) {
  const root = panel.shadowRoot;
  const dialog = root?.querySelector("#tool-dialog");
  const editor = getToolYamlEditor(panel);
  const status = root?.querySelector("#tool-error");
  if (!dialog || !editor?.textarea) return;
  panel._repairToolIndex = Number(item.index);
  const loadToken = {};
  panel._repairToolLoad = loadToken;
  const agentId = panel._agentId;
  const isCurrent = () => dialog.open && panel._repairToolLoad === loadToken
    && panel._agentId === agentId && root.querySelector("#tool-dialog") === dialog;
  panel._repairToolRevision = panel._result?.revision;
  panel._toolOriginalName = null;
  panel._toolInitialYaml = item.yaml || JSON.stringify(item.tool || {}, null, 2);
  panel._toolReplaceableYaml = null;
  editor.setYaml(panel._toolInitialYaml);
  const picker = root.querySelector("#built-in-function");
  if (picker) picker.value = "";
  const pickerContainer = root.querySelector("#built-in-picker");
  if (pickerContainer) pickerContainer.hidden = false;
  if (status) {
    status.className = "validation invalid";
    status.textContent = item.validation_error || "This Function Tool needs repair.";
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.open = true;
  editor.focus?.();
  if (picker) {
    picker.disabled = true;
    void Promise.all([
      import("./agent-config-tools-base.js"),
      panel._call("tools", "built_in_catalog", {tools: panel._result?.config?.functions || []}),
    ]).then(([{populateToolPresetPicker}, catalog]) => {
      if (isCurrent()) {
        populateToolPresetPicker(panel, catalog);
        picker.disabled = false;
      }
    }).catch(() => { /* Keep manual YAML repair available if presets cannot load. */ });
  }
  try {
    const {bindNativeToolYaml} = await import("./agent-config-native-yaml.js");
    if (isCurrent()) bindNativeToolYaml(panel);
  } catch (_err) {
    // The textarea stays usable when the optional native editor cannot load.
  }
}

export async function refreshAfterRepair(panel, message, saved, submission = captureConfigurationSubmission(panel)) {
  return completeConfigurationAcknowledgement(panel, saved, submission, {
    fields:["functions", "function_groups"], metadata:["function_repair"], message,
    refresh:async () => {
      if (panel._viewKey && panel._viewKey() !== "capabilities/functions") return;
      if (panel._configData) panel._result = panel._configData;
      await panel._loadAgents(submission.agentId, submission);
      // The catalogue loader handles transport failures by rendering _error.
      if (panel._error) throw new Error(panel._error);
    },
  });
}

async function saveInvalidTool(panel, button) {
  const index = panel._repairToolIndex;
  if (!Number.isInteger(index)) return false;
  const editor = getToolYamlEditor(panel);
  const yaml = editor?.getYaml?.() || "";
  const submission = captureConfigurationSubmission(panel);
  if (typeof panel._setSaving === "function") panel._setSaving(button, true);
  else button.disabled = true;
  try {
    const validation = await panel._call("tools", "validate_yaml", {yaml});
    if (!configurationSubmissionIsCurrent(panel, submission)) return true;
    if (!validation?.valid) {
      const detail = Object.values(validation?.errors || {})[0] || "Function Tool is still invalid.";
      const status = panel.shadowRoot?.querySelector("#tool-error");
      if (status) {
        status.className = "validation invalid";
        status.textContent = detail;
      }
      return true;
    }
    const saved = await panel._call("function_repair", "save_one", {
      index,
      tool: validation.config,
      revision: panel._repairToolRevision,
    });
    if (!configurationSubmissionIsCurrent(panel, submission)) return true;
    panel.shadowRoot?.querySelector("#tool-dialog")?.close?.();
    panel._repairToolIndex = null;
    await refreshAfterRepair(panel, "Function Tool repaired", saved, submission);
  } catch (err) {
    if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to repair Function Tool: ${err.message || String(err)}`, true);
  } finally {
    if (typeof panel._setSaving === "function") panel._setSaving(button, false);
    else button.disabled = false;
  }
  return true;
}

async function deleteInvalidTool(panel, item, button) {
  const submission = captureConfigurationSubmission(panel);
  const label = item.name || `Function Tool ${Number(item.index) + 1}`;
  const confirmed = typeof panel._confirm === "function"
    ? await panel._confirm(`Delete ${label}?`, "Its retained Function Group assignment will also be removed.")
    : globalThis.confirm?.(`Delete ${label}?`) !== false;
  if (!confirmed || !configurationSubmissionIsCurrent(panel, submission)) return;
  button.disabled = true;
  try {
    const saved = await panel._call("function_repair", "delete_one", {
      index: Number(item.index),
      revision: panel._result?.revision,
    });
    await refreshAfterRepair(panel, "Function Tool deleted", saved, submission);
  } catch (err) {
    if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to delete Function Tool: ${err.message || String(err)}`, true);
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
      const submission = captureConfigurationSubmission(panel);
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
        const saved = await panel._call("function_repair", "save", {tools, revision: panel._result?.revision});
        await refreshAfterRepair(panel, "Function Tools repaired", saved, submission);
      } catch (err) {
        if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to repair Function Tools: ${err.message || String(err)}`, true);
      } finally {
        button.disabled = false;
      }
    })();
  });
}

export {
  bindFallbackRepair,
  bindIsolatedRepair,
  renderFunctionRepairCards,
  requiresToolRepairFallback,
  editableToolsText,
  functionRepairView,
  invalidToolCards,
  repairIssue,
  repairMetadata,
  renderFallbackRepair,
};

export function bindFunctionRepair(panel) {
  if (!repairIssue(panel) || !functionRepairView(panel)) return;
  if (requiresToolRepairFallback(panel)) bindFallbackRepair(panel);
  else bindIsolatedRepair(panel);
}
