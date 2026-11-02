import {UnsavedState} from "./unsaved-state.js";
import {captureConfigurationOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement} from "./configuration-acknowledgement.js";
import {getToolYamlEditor, captureToolEditorOwner, captureToolEditorOperation, acknowledgeToolEditor} from "./tool-yaml-editor-adapter.js";

function repairIssue(panel) {
  const issue = panel._selectedAgent?.()?.configuration_issue;
  if (functionRepairView(panel) && panel._result?.config) {
    // Direct Functions visits may precede the Overview health read. The loaded
    // configuration is authoritative, including when an earlier issue was fixed.
    const repair = repairMetadata(panel);
    if (!repair) return hasRepairDraft(panel)
      ? {field:"functions", message:"Review your unsaved Function Tools changes.", repairable:true}
      : null;
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


function ownRepairDraft(panel, initial = null) {
  const previous = panel._functionRepairDraft;
  if (initial || !previous || previous.agent !== panel._agentId
      || (previous.text === previous.baseline && previous.revision !== panel._result?.revision)) {
    panel._functionRepairDraft = initial || {
      agent:panel._agentId, text:editableToolsText(repairMetadata(panel)?.tools_yaml ?? panel._result?.tools_yaml ?? repairMetadata(panel)?.tools ?? panel._result?.tools),
      baseline:editableToolsText(repairMetadata(panel)?.tools_yaml ?? panel._result?.tools_yaml ?? repairMetadata(panel)?.tools ?? panel._result?.tools), revision:panel._result?.revision,
    };
  }
  const draft = panel._functionRepairDraft;
  const state = panel._unsavedState ||= new UnsavedState();
  state.register("function-repair", {
    dirty:() => panel._agentId === draft.agent && draft.text !== draft.baseline,
    owns:destination => Boolean(destination), destinations:() => ["capabilities/functions"],
    get pending() { return Boolean(draft.pending); },
    discard:() => { draft.text = draft.baseline; const editor=panel.shadowRoot?.querySelector("#function-repair-editor"); if(editor)editor.value=draft.text; },
    restore:target => {
      if (!target._data?.is_admin || target._functionRepairDraft?.text !== target._functionRepairDraft?.baseline) return;
      ownRepairDraft(target, {...draft});
      draft.text = draft.baseline;
      target._render();
    },
  });
  return draft;
}

function renderFallbackRepair(panel, issue) {
  const draft = ownRepairDraft(panel);
  const result = panel._result || {};
  const repair = repairMetadata(panel) || {};
  const recovering = Boolean(repairMetadata(panel));
  const validationError = recovering
    ? repair.validation_error || result.validation_error || issue.message || "The saved Function Tools are invalid."
    : issue.message;
  return `<section class="card function-repair" aria-labelledby="function-repair-title">
    <h2 id="function-repair-title">${recovering ? "Function Tools need repair" : "Unsaved Function Tools changes"}</h2>
    <div class="error" role="alert">${escapeHtml(panel, validationError)}</div>
    <p>${recovering ? "This recovery editor appears only when the saved Function Tools cannot be repaired individually." : "These edits were made after your last submission. Review and save them, or discard them to return to the saved Function Tools."}</p>
    <label class="field"><span>Saved Function Tools (YAML or JSON)</span><textarea id="function-repair-editor" rows="20" spellcheck="false">${escapeHtml(panel, draft.text)}</textarea></label>
    <div class="actions"><button id="function-repair-save" type="button" ${draft.pending ? "disabled" : ""}>Validate and save repair</button><button id="function-repair-discard" type="button" class="secondary" ${draft.pending ? "disabled" : ""}>Discard unsaved repair</button></div>
  </section>`;
}

function hasRepairDraft(panel) {
  const draft = panel._functionRepairDraft;
  return Boolean(draft && draft.agent === panel._agentId && draft.text !== draft.baseline);
}

function requiresToolRepairFallback(panel) {
  const repair = repairMetadata(panel);
  return hasRepairDraft(panel) || (repair?.isolatable === false && repair.tools_valid !== true);
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

async function openInvalidToolEditor(panel, item, revision = panel._result?.revision) {
  const root = panel.shadowRoot;
  const dialog = root?.querySelector("#tool-dialog");
  const editor = getToolYamlEditor(panel);
  const status = root?.querySelector("#tool-error");
  if (!dialog || !editor?.textarea) return;
  panel._repairToolIndex = Number(item.index);
  panel._repairToolLoad = {owner:captureConfigurationOwner(panel)};
  panel._toolEditorLoad = null;
  const {isCurrent} = captureToolEditorOwner(panel);
  panel._repairToolRevision = revision;
  const save = root.querySelector("#tool-save");
  if (save) {
    if (typeof panel._setSaving === "function") panel._setSaving(save, false);
    else save.disabled = false;
  }
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

export async function refreshAfterRepair(panel, message, saved, submission = captureConfigurationSubmission(panel), {reload = true, refresh} = {}) {
  return completeConfigurationAcknowledgement(panel, saved, submission, {
    fields:["functions", "function_groups"], metadata:["function_repair"], message,
    refresh:async () => {
      await refresh?.();
      if (panel._viewKey && panel._viewKey() !== "capabilities/functions") return;
      if (panel._configData) panel._result = panel._configData;
      if (!reload) {
        // The acknowledgement already contains the authoritative repair and
        // agent snapshots. Do not reload the panel over a newer editor.
        if (panel.shadowRoot?.querySelector("dialog[open]")) panel._eocDeferredEditorRender = true;
        else panel._render();
        return;
      }
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
  const operation = captureToolEditorOperation(panel);
  if (!operation.isCurrent()) {
    panel._toast?.("The Function Tool editor owner changed. Reopen it before continuing.", true);
    return true;
  }
  const submission = captureConfigurationSubmission(panel);
  let finishingOperation = operation;
  if (typeof panel._setSaving === "function") panel._setSaving(button, true);
  else button.disabled = true;
  try {
    const validation = await panel._call("tools", "validate_yaml", {yaml});
    if (!operation.isUnchanged()) return true;
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
      revision:operation.revision,
    });
    if (!configurationSubmissionIsCurrent(panel, submission)) return true;
    const previous = {revision:panel._configData?.revision, config:panel._configData?.config};
    await refreshAfterRepair(panel, "Function Tool repaired", saved, submission, {reload:false, refresh:() => {
      const closeAfterAcknowledgement = operation.isUnchanged();
      finishingOperation = acknowledgeToolEditor(panel, operation, saved, previous);
      if (closeAfterAcknowledgement) {
        operation.dialog.close();
        panel._repairToolIndex = null;
      }
    }});
  } catch (err) {
    if (operation.isUnchanged()) panel._toast(`Unable to repair Function Tool: ${err.message || String(err)}`, true);
  } finally {
    if (finishingOperation.isCurrent()) {
      if (typeof panel._setSaving === "function") panel._setSaving(button, false);
      else button.disabled = false;
    }
  }
  return true;
}

async function deleteInvalidTool(panel, item, button, revision = panel._result?.revision) {
  const submission = captureConfigurationSubmission(panel);
  const user = panel.hass?.user?.id;
  const label = item.name || `Function Tool ${Number(item.index) + 1}`;
  const confirmed = typeof panel._confirm === "function"
    ? await panel._confirm(`Delete ${label}?`, "Its retained Function Group assignment will also be removed.")
    : globalThis.confirm?.(`Delete ${label}?`) !== false;
  if (!confirmed || panel.isConnected === false || panel.hass?.user?.id !== user
      || !configurationSubmissionIsCurrent(panel, submission)) return;
  button.disabled = true;
  try {
    const saved = await panel._call("function_repair", "delete_one", {
      index: Number(item.index),
      revision,
    });
    // The complete repair acknowledgement owns this deletion. A catalogue
    // refresh can otherwise replace an independently opened sibling editor,
    // including after a transient read failure. Keep its authoring revision.
    await refreshAfterRepair(panel, "Function Tool deleted", saved, submission, {reload:false});
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
  // A raw index identifies an item only within this saved repair snapshot.
  // Refreshes can shift indices while a confirmation or another editor is open.
  const revision = panel._result?.revision;
  const root = panel.shadowRoot;
  const published = !panel._busy && !panel._error && !panel._eocDeferredEditorRender
    && (!panel._eocRenderedRoute || panel._eocRenderedRoute === `${panel._agentId}|${panel._viewKey()}`);
  const bindRepairAction = (selector, edit) => root?.querySelectorAll?.(selector).forEach((button) => {
    // Identical markup can preserve this button after an accepted read. Refresh
    // its snapshot only when that read has actually been published; deferred
    // old cards must not acquire a newer raw collection's revision.
    if (published) button.__eocRepairSnapshot = {
      item:byIndex.get(Number(button.dataset.repairIndex)), revision,
      agentId:panel._agentId, entryId:panel._selectedAgent?.()?.entry_id, user:panel.hass?.user?.id,
    };
    if (!button.__eocRepairSnapshot || button.dataset.eocBound === "true") return;
    button.dataset.eocBound = "true";
    button.addEventListener("click", () => {
      const snapshot = button.__eocRepairSnapshot;
      if (!snapshot?.item || button.isConnected === false || panel.hass?.user?.id !== snapshot.user
          || !configurationSubmissionIsCurrent(panel, snapshot)) return;
      if (edit) openInvalidToolEditor(panel, snapshot.item, snapshot.revision);
      else void deleteInvalidTool(panel, snapshot.item, button, snapshot.revision);
    });
  });
  bindRepairAction(".edit-invalid-tool", true);
  bindRepairAction(".delete-invalid-tool", false);
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
    dialog.addEventListener("close", event => {
      if (event.target !== root.querySelector("#tool-dialog") || event.target.open) return;
      panel._repairToolIndex = null;
      panel._repairToolLoad = null;
      panel._repairToolRevision = null;
    });
  }
}

function bindFallbackRepair(panel) {
  const button = panel.shadowRoot?.querySelector?.("#function-repair-save");
  if (!button || button.dataset.eocBound === "true") return;
  button.dataset.eocBound = "true";
  const draft = ownRepairDraft(panel);
  const editor = panel.shadowRoot?.querySelector("#function-repair-editor");
  editor?.addEventListener?.("input", () => { draft.text = editor.value; });
  panel.shadowRoot?.querySelector("#function-repair-discard")?.addEventListener?.("click", () => {
    if (draft.pending) return;
    panel._unsavedState.scopes.get("function-repair").discard();
    panel._render();
  });
  button.addEventListener("click", () => {
    if (draft.pending) return;
    void (async () => {
      const submission = captureConfigurationSubmission(panel);
      // Malformed persisted YAML is displayed verbatim. The backend owns YAML/JSON
      // parsing and validation so the repair can be saved in the displayed format.
      const tools = panel.shadowRoot?.querySelector?.("#function-repair-editor")?.value || "";
      draft.text = tools;
      draft.pending = true;
      button.disabled = true;
      const discard = panel.shadowRoot?.querySelector("#function-repair-discard");
      if (discard) discard.disabled = true;
      try {
        const saved = await panel._call("function_repair", "save", {tools, revision: draft.revision});
        draft.baseline = tools;
        draft.revision = saved.revision;
        await refreshAfterRepair(panel, "Function Tools repaired", saved, submission);
      } catch (err) {
        if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to repair Function Tools: ${err.message || String(err)}`, true);
      } finally {
        draft.pending = false;
        button.disabled = false;
        if (configurationSubmissionIsCurrent(panel, submission) && functionRepairView(panel)) {
          for (const id of ["function-repair-save", "function-repair-discard"]) {
            const current = panel.shadowRoot?.querySelector(`#${id}`);
            if (current) current.disabled = false;
          }
        }
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
