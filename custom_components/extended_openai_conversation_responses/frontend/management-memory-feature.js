export * from "./guest-mode-ui.js";
export * from "./management-temporary-memory.js";
import {reconcilePersistentMemories, hasPersistentMemoryCollection} from "./guest-mode-ui.js";
import {reconcileTemporaryMemories, hasTemporaryMemoryCollection} from "./management-temporary-memory.js";

export function reconcileMemories(panel) {
  return panel._memoryKind === "temporary" ? reconcileTemporaryMemories(panel) : reconcilePersistentMemories(panel);
}

export function hasMemoryCollection(panel) {
  return panel._memoryKind === "temporary" ? hasTemporaryMemoryCollection(panel) : hasPersistentMemoryCollection(panel);
}

// Kept with the lazy Memory route; the shared shell only calls these when loaded.
export function memoryMetadataFields() {
  return `<label>Importance<select id="memory-importance"><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select></label>
    <label>Owner<select id="memory-owner"></select></label>
    <label>Subject<input id="memory-subject"></label><label>Key<input id="memory-key"></label>
    <label>Valid from<input id="memory-valid-from" placeholder="ISO 8601 date-time with timezone"></label>
    <label class="toggle"><span>Refresh confirmation on save</span><input id="memory-refresh-confirmation" type="checkbox"></label>
    <p id="memory-confirmation" class="help"></p>`;
}

export function populateMemoryMetadata(panel, memory) {
  const root = panel.shadowRoot;
  if (!root.querySelector("#memory-importance")) root.querySelector("#memory-metadata").innerHTML = memoryMetadataFields();
  for (const field of ["importance", "subject", "key", "valid_from"]) {
    root.querySelector(`#memory-${field.replaceAll("_", "-")}`).value = memory?.[field] || (field === "importance" ? "normal" : "");
  }
  const scopes = (panel._data?.scopes || []).filter(scope => scope.scope_id === panel._scopeId ||
    (panel._data?.is_admin && (panel._scopeId === "shared:household" ? scope.scope_type === "user" : panel._scopeId.startsWith("user:") && scope.scope_id === "shared:household")));
  if (!scopes.some(scope => scope.scope_id === panel._scopeId)) scopes.unshift({scope_id: panel._scopeId, display_name: panel._scopeId});
  const owner = root.querySelector("#memory-owner");
  owner.innerHTML = scopes.map(scope => `<option value="${panel._e(scope.scope_id)}">${panel._e(scope.display_name)}</option>`).join("");
  owner.value = panel._memoryEditorScope;
  root.querySelector("#memory-refresh-confirmation").checked = false;
  root.querySelector("#memory-refresh-confirmation").disabled = !memory;
  root.querySelector("#memory-confirmation").textContent = memory
    ? `Last confirmed: ${memory.last_confirmed_at ? panel._formatDate(memory.last_confirmed_at) : "Never"}. Editing preserves confirmation unless explicitly refreshed.`
    : "New memories are confirmed when created.";
}

export function memoryMetadataValues(panel) {
  const root = panel.shadowRoot;
  return {
    importance: root.querySelector("#memory-importance").value,
    target_scope_id: root.querySelector("#memory-owner").value,
    subject: root.querySelector("#memory-subject").value,
    key: root.querySelector("#memory-key").value,
    valid_from: root.querySelector("#memory-valid-from").value,
    refresh_confirmation: root.querySelector("#memory-refresh-confirmation").checked,
  };
}

export function memoryMutationValues(values, memory) {
  const result = {content: values.content.trim(), category: values.category.trim() || "general", importance: values.importance, target_scope_id: values.target_scope_id};
  const clear = [];
  for (const field of ["subject", "key", "valid_from"]) {
    const value = values[field].trim();
    if (value) result[field] = value;
    else if (memory?.[field]) clear.push(field);
  }
  if (memory) {
    result.refresh_confirmation = values.refresh_confirmation;
    if (clear.length) result.clear_fields = clear;
  }
  return result;
}
