const CONTENT_LIMIT = 500;
const CATEGORY_LIMIT = 64;

function validOwnerScope(scope) {
  return scope?.scope_type === "user" || scope?.scope_type === "shared";
}

function ownerLabel(panel, ownerScopeId) {
  const known = (panel._data?.scopes || []).find((scope) => scope.scope_id === ownerScopeId);
  if (known) return known.display_name;
  if (ownerScopeId === "shared:household") return "Shared household";
  return String(ownerScopeId || "").startsWith("user:") ? "Personal" : "Unavailable";
}

function ensureTemporaryScope(panel) {
  const scopes = (panel._data?.scopes || []).filter(validOwnerScope);
  if (scopes.some((scope) => scope.scope_id === panel._scopeId)) return;
  const current = scopes.find((scope) => scope.is_current_user) || scopes[0];
  if (current) panel._scopeId = current.scope_id;
}

function temporaryScopeOptions(panel) {
  return (panel._data?.scopes || [])
    .filter(validOwnerScope)
    .map((scope) => `<option value="${panel._e(scope.scope_id)}" ${scope.scope_id === panel._scopeId ? "selected" : ""}>${panel._e(scope.display_name)}</option>`)
    .join("");
}

function renderTemporaryMemories(panel) {
  const items = panel._filtered(
    panel._result?.memories || [],
    (item) => `${item.content} ${item.category} ${item.owner_scope_id || ""}`,
  );
  const stats = panel._result?.stats || {};
  const pruned = Number(stats.invalid_owner_records_pruned || 0);
  const overflow = Number(stats.startup_overflow_records_pruned || 0);
  const diagnostics = pruned || overflow
    ? `<p class="help">Startup cleanup removed ${panel._e(String(pruned))} record(s) with invalid legacy ownership and ${panel._e(String(overflow))} record(s) above the 100-record ceiling.</p>`
    : "";
  return `<section class="content-card">
    <div class="section-heading"><div><h2>Memories</h2><p>Short-term details that are removed automatically at their expiry time.</p></div></div>
    <div class="config-jumps"><button type="button" class="secondary memory-kind" data-kind="persistent">Long-term</button><button type="button" class="secondary memory-kind" data-kind="temporary" disabled>Short-term</button></div>
    <p class="help">Short-term memories belong to a Personal or Shared scope. Conversation and device continuity do not determine ownership. Existing records remain manageable until they expire even when Temporary Memory is turned off.</p>
    ${diagnostics}
    <input id="list-search" class="search" type="search" value="${panel._e(panel._query)}" placeholder="Search memories" aria-label="Search memories">
    <div class="list memory-list">${items.map((memory) => `<article class="list-card">
      <div class="card-main clickable edit-temporary-memory" tabindex="0" role="button" data-id="${panel._e(memory.memory_id)}">
        <p class="primary-copy">${panel._e(memory.content)}</p>
        <p class="meta">${panel._e(memory.category)} · ${panel._e(ownerLabel(panel, memory.owner_scope_id))} · Expires ${panel._e(panel._formatDate(memory.expires_at))}</p>
      </div>
      <div class="actions"><button type="button" class="secondary edit-temporary-memory" data-id="${panel._e(memory.memory_id)}">Edit</button><button type="button" class="danger delete-temporary" data-id="${panel._e(memory.memory_id)}">Delete</button></div>
    </article>`).join("") || panel._empty(panel._query ? "No short-term memories match this filter." : "No short-term memories in this scope.")}</div>
  </section>`;
}

function temporaryDialog(panel) {
  return `<dialog id="temporary-memory-dialog" class="editor-dialog" aria-labelledby="temporary-memory-dialog-title">
    <form id="temporary-memory-form">
      <div class="dialog-header"><h2 id="temporary-memory-dialog-title">Edit short-term memory</h2><button type="button" class="icon close-temporary-editor" aria-label="Close">×</button></div>
      <div class="dialog-body">
        <label>Memory<textarea id="temporary-memory-content" maxlength="${CONTENT_LIMIT}" required spellcheck="true"></textarea></label>
        <label>Category<input id="temporary-memory-category" maxlength="${CATEGORY_LIMIT}" required></label>
        <label>Expires at<input id="temporary-memory-expiry" type="text" required placeholder="2026-09-07T18:30:00+01:00"></label>
        <p class="help">Use an ISO 8601 date-time including its timezone. Expiry must remain in the future and within the existing one-year Temporary Memory limit.</p>
        <p id="temporary-memory-meta" class="meta"></p>
        <div id="temporary-memory-error" class="inline-error" role="alert"></div>
      </div>
      <div class="dialog-actions"><button type="button" id="temporary-memory-delete" class="danger">Delete</button><button type="button" class="secondary close-temporary-editor">Cancel</button><button type="submit" id="temporary-memory-save">Save</button></div>
    </form>
  </dialog>`;
}

export function openTemporaryMemory(panel, memoryId) {
  const memory = (panel._result?.memories || []).find((item) => item.memory_id === memoryId);
  if (!memory) return;
  panel._temporaryMemoryDraft = {
    memory_id: memory.memory_id,
    content: memory.content || "",
    category: memory.category || "general",
    expires_at: memory.expires_at || "",
    owner_scope_id: memory.owner_scope_id || panel._scopeId,
  };
  const dialog = panel.shadowRoot.querySelector("#temporary-memory-dialog");
  panel.shadowRoot.querySelector("#temporary-memory-content").value = panel._temporaryMemoryDraft.content;
  panel.shadowRoot.querySelector("#temporary-memory-category").value = panel._temporaryMemoryDraft.category;
  panel.shadowRoot.querySelector("#temporary-memory-expiry").value = panel._temporaryMemoryDraft.expires_at;
  panel.shadowRoot.querySelector("#temporary-memory-meta").textContent = `Owner: ${ownerLabel(panel, panel._temporaryMemoryDraft.owner_scope_id)}`;
  panel.shadowRoot.querySelector("#temporary-memory-error").textContent = "";
  dialog?.showModal();
}

export function temporaryMemoryDirty(panel) {
  const draft = panel._temporaryMemoryDraft;
  if (!draft) return false;
  return panel.shadowRoot.querySelector("#temporary-memory-content")?.value !== draft.content
    || panel.shadowRoot.querySelector("#temporary-memory-category")?.value !== draft.category
    || panel.shadowRoot.querySelector("#temporary-memory-expiry")?.value !== draft.expires_at;
}

export async function closeTemporaryMemory(panel, force = false) {
  const dialog = panel.shadowRoot.querySelector("#temporary-memory-dialog");
  if (force) dialog?.close();
  else if (!await panel._confirmEditorClose(dialog)) return false;
  panel._temporaryMemoryDraft = null;
  return true;
}

export async function saveTemporaryMemory(panel) {
  const draft = panel._temporaryMemoryDraft;
  if (!draft || panel._temporaryMemorySaving) return;
  const content = panel.shadowRoot.querySelector("#temporary-memory-content")?.value ?? "";
  const category = panel.shadowRoot.querySelector("#temporary-memory-category")?.value ?? "";
  const expiresAt = panel.shadowRoot.querySelector("#temporary-memory-expiry")?.value ?? "";
  const error = panel.shadowRoot.querySelector("#temporary-memory-error");
  if (!content.trim() || !category.trim() || !expiresAt.trim()) {
    error.textContent = "Memory, category, and expiry are required.";
    return;
  }
  panel._temporaryMemorySaving = true;
  const save = panel.shadowRoot.querySelector("#temporary-memory-save");
  panel._setSaving(save, true);
  try {
    await panel._call("memories", "temporary_update", {
      scope_id: panel._scopeId,
      memory_id: draft.memory_id,
      content,
      category,
      expires_at: expiresAt,
    });
    await panel._closeTemporaryMemory(true);
    await panel._refreshAfterMutation();
    panel._toast("Short-term memory updated");
  } catch (err) {
    error.textContent = err.message || String(err);
  } finally { panel._temporaryMemorySaving = false; panel._setSaving(save, false); }
}

export async function deleteTemporaryMemory(panel, memoryId) {
  if (!memoryId || !await panel._confirm(
    "Delete temporary memory?",
    "This short-lived fact will no longer be included in later requests.",
    "Delete",
  )) return false;
  try {
    await panel._call("memories", "temporary_delete", {
      scope_id: panel._scopeId,
      memory_id: memoryId,
    });
    if (panel._temporaryMemoryDraft?.memory_id === memoryId) {
      await panel._closeTemporaryMemory(true);
    }
    await panel._refreshAfterMutation();
    panel._toast("Temporary memory deleted");
    return true;
  } catch (err) {
    panel._toast(`Unable to delete temporary memory: ${err.message || String(err)}`, true);
    return false;
  }
}

export function bindTemporaryMemory(panel) {
  if (panel._viewKey?.() !== "data-memory/memories" || panel._memoryKind !== "temporary") return;
  panel.shadowRoot.querySelectorAll(".edit-temporary-memory").forEach((element) => {
    panel._activate(element, () => panel._openTemporaryMemory(element.dataset.id));
  });
  panel.shadowRoot.querySelector("#temporary-memory-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    panel._saveTemporaryMemory();
  });
  panel.shadowRoot.querySelectorAll(".close-temporary-editor").forEach((button) => {
    button.addEventListener("click", () => panel._closeTemporaryMemory(!button.classList.contains("icon")));
  });
  panel.shadowRoot.querySelector("#temporary-memory-delete")?.addEventListener("click", () => {
    const id = panel._temporaryMemoryDraft?.memory_id;
    if (id) panel._deleteTemporaryMemory(id);
  });
  return;
}

export function renderTemporaryScopePicker(panel) {
  ensureTemporaryScope(panel);
  return `<section class="scope-bar"><label><span>Show short-term memories belonging to</span><select id="scope">${temporaryScopeOptions(panel)}</select></label>${panel._data?.is_admin ? `<small>Temporary Memory ownership is limited to Personal and Shared scopes.</small>` : ""}</section>`;
}

export {
  temporaryDialog,
  CATEGORY_LIMIT,
  CONTENT_LIMIT,
  ensureTemporaryScope,
  ownerLabel,
  renderTemporaryMemories,
  temporaryScopeOptions,
  validOwnerScope,
};
