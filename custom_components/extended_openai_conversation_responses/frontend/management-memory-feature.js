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

// Kept with the lazy Memory route.
export {memoryExpiryISO, memoryLocalDateTime} from "./memory-datetime.js";
export function memoryMetadataFields() {
  return `<label>Type<select id="memory-type"><option value="persistent">Long-term</option><option value="temporary">Short-term</option></select></label>
    <label>Category<input id="memory-category" value="general" maxlength="64" required></label>
    <label>Owner<select id="memory-owner"></select></label>
    <label id="memory-expiry-field" hidden>Expires<input id="memory-expiry" type="datetime-local" step="1"><small>Home Assistant local time. Choose a future time within one year.</small></label>
    <div id="memory-advanced" hidden><label>Subject<input id="memory-subject"></label><label>Key<input id="memory-key"></label>
    <label>Valid from<input id="memory-valid-from" placeholder="ISO 8601 date-time with timezone"></label></div>`;
}

export function populateMemoryMetadata(panel, memory) {
  const root = panel.shadowRoot;
  if (!root.querySelector("#memory-type")) root.querySelector("#memory-metadata").innerHTML = memoryMetadataFields();
  for (const field of ["subject", "key", "valid_from"]) {
    root.querySelector(`#memory-${field.replaceAll("_", "-")}`).value = memory?.[field] || "";
  }
  const scopes = (panel._data?.scopes || []).filter(scope => scope.scope_id === panel._scopeId ||
    (panel._data?.is_admin && (panel._scopeId === "shared:household" ? scope.scope_type === "user" : panel._scopeId.startsWith("user:") && scope.scope_id === "shared:household")));
  if (!scopes.some(scope => scope.scope_id === panel._scopeId)) scopes.unshift({scope_id: panel._scopeId, display_name: panel._scopeId});
  const owner = root.querySelector("#memory-owner");
  owner.innerHTML = scopes.map(scope => `<option value="${panel._e(scope.scope_id)}">${panel._e(scope.display_name)}</option>`).join("");
  owner.value = panel._memoryEditorScope;
  const type = root.querySelector("#memory-type");
  type.value = memory ? "persistent" : panel._memoryKind;
  type.disabled = Boolean(memory);
  root.querySelector("#memory-advanced").hidden = !memory;
  root.querySelector("#memory-expiry").value = "";
  const updateType = () => {
    const temporary = type.value === "temporary";
    const selectedOwner = owner.value;
    const available = temporary ? (panel._data?.scopes || []).filter(scope =>
      ["user", "shared"].includes(scope.scope_type) && (panel._data?.is_admin || scope.scope_id === panel._scopeId)) : scopes;
    owner.innerHTML = available.map(scope => `<option value="${panel._e(scope.scope_id)}">${panel._e(scope.display_name)}</option>`).join("");
    if (available.some(scope => scope.scope_id === selectedOwner)) owner.value = selectedOwner;
    else owner.value = available.find(scope => scope.is_current_user)?.scope_id || available[0]?.scope_id || "";
    root.querySelector("#memory-expiry-field").hidden = !temporary;
    root.querySelector("#memory-expiry").required = temporary;
    root.querySelector("#memory-content").maxLength = temporary ? 500 : 1000;
  };
  type.onchange = updateType;
  updateType();
}

export function memoryMetadataValues(panel) {
  const root = panel.shadowRoot;
  return {
    type: root.querySelector("#memory-type").value,
    expires_at: root.querySelector("#memory-expiry").value,
    target_scope_id: root.querySelector("#memory-owner").value,
    subject: root.querySelector("#memory-subject").value,
    key: root.querySelector("#memory-key").value,
    valid_from: root.querySelector("#memory-valid-from").value,
  };
}

export function memoryMutationValues(values, memory) {
  const result = {content: values.content.trim(), category: values.category.trim() || "general", target_scope_id: values.target_scope_id};
  if (values.type === "temporary") return result;
  const clear = [];
  for (const field of ["subject", "key", "valid_from"]) {
    const value = values[field].trim();
    if (value) result[field] = value;
    else if (memory?.[field]) clear.push(field);
  }
  if (memory && clear.length) result.clear_fields = clear;
  return result;
}
