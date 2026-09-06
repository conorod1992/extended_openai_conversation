const PATCHED = Symbol.for("extended-openai.management-temporary-memory");
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

export function installManagementTemporaryMemory(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalLoadSection = prototype._loadSection;
    prototype._loadSection = async function(...args) {
      if (this._viewKey?.() === "data-memory/memories" && this._memoryKind === "temporary") {
        ensureTemporaryScope(this);
      }
      return originalLoadSection.apply(this, args);
    };

    const originalScopePicker = prototype._scopePicker;
    prototype._scopePicker = function(...args) {
      if (this._viewKey?.() !== "data-memory/memories" || this._memoryKind !== "temporary") {
        return originalScopePicker.apply(this, args);
      }
      ensureTemporaryScope(this);
      return `<section class="scope-bar"><label><span>Show short-term memories belonging to</span><select id="scope">${temporaryScopeOptions(this)}</select></label>${this._data?.is_admin ? `<small>Temporary Memory ownership is limited to Personal and Shared scopes.</small>` : ""}</section>`;
    };

    const originalMemories = prototype._memories;
    prototype._memories = function(...args) {
      return this._memoryKind === "temporary"
        ? renderTemporaryMemories(this)
        : originalMemories.apply(this, args);
    };

    const originalDialogs = prototype._dialogs;
    prototype._dialogs = function(...args) {
      return `${originalDialogs.apply(this, args)}${temporaryDialog(this)}`;
    };

    prototype._openTemporaryMemory = function(memoryId) {
      const memory = (this._result?.memories || []).find((item) => item.memory_id === memoryId);
      if (!memory) return;
      this._temporaryMemoryDraft = {
        memory_id: memory.memory_id,
        content: memory.content || "",
        category: memory.category || "general",
        expires_at: memory.expires_at || "",
        owner_scope_id: memory.owner_scope_id || this._scopeId,
      };
      const dialog = this.shadowRoot.querySelector("#temporary-memory-dialog");
      this.shadowRoot.querySelector("#temporary-memory-content").value = this._temporaryMemoryDraft.content;
      this.shadowRoot.querySelector("#temporary-memory-category").value = this._temporaryMemoryDraft.category;
      this.shadowRoot.querySelector("#temporary-memory-expiry").value = this._temporaryMemoryDraft.expires_at;
      this.shadowRoot.querySelector("#temporary-memory-meta").textContent = `Owner: ${ownerLabel(this, this._temporaryMemoryDraft.owner_scope_id)}`;
      this.shadowRoot.querySelector("#temporary-memory-error").textContent = "";
      dialog?.showModal();
    };

    prototype._temporaryMemoryDirty = function() {
      const draft = this._temporaryMemoryDraft;
      if (!draft) return false;
      return this.shadowRoot.querySelector("#temporary-memory-content")?.value !== draft.content
        || this.shadowRoot.querySelector("#temporary-memory-category")?.value !== draft.category
        || this.shadowRoot.querySelector("#temporary-memory-expiry")?.value !== draft.expires_at;
    };

    prototype._closeTemporaryMemory = async function(force = false) {
      if (!force && this._temporaryMemoryDirty()) {
        const discard = await this._confirm(
          "Discard unsaved changes?",
          "Your edits to this short-term memory have not been saved.",
          "Discard",
        );
        if (!discard) return false;
      }
      this.shadowRoot.querySelector("#temporary-memory-dialog")?.close();
      this._temporaryMemoryDraft = null;
      return true;
    };

    prototype._saveTemporaryMemory = async function() {
      const draft = this._temporaryMemoryDraft;
      if (!draft) return;
      const content = this.shadowRoot.querySelector("#temporary-memory-content")?.value ?? "";
      const category = this.shadowRoot.querySelector("#temporary-memory-category")?.value ?? "";
      const expiresAt = this.shadowRoot.querySelector("#temporary-memory-expiry")?.value ?? "";
      const error = this.shadowRoot.querySelector("#temporary-memory-error");
      if (!content.trim() || !category.trim() || !expiresAt.trim()) {
        error.textContent = "Memory, category, and expiry are required.";
        return;
      }
      try {
        await this._call("memories", "temporary_update", {
          scope_id: this._scopeId,
          memory_id: draft.memory_id,
          content,
          category,
          expires_at: expiresAt,
        });
        await this._closeTemporaryMemory(true);
        await this._refreshAfterMutation();
        this._toast("Short-term memory updated");
      } catch (err) {
        error.textContent = err.message || String(err);
      }
    };

    prototype._deleteTemporaryMemory = async function(memoryId) {
      if (!memoryId || !await this._confirm(
        "Delete temporary memory?",
        "This short-lived fact will no longer be included in later requests.",
        "Delete",
      )) return false;
      try {
        await this._call("memories", "temporary_delete", {
          scope_id: this._scopeId,
          memory_id: memoryId,
        });
        if (this._temporaryMemoryDraft?.memory_id === memoryId) {
          await this._closeTemporaryMemory(true);
        }
        await this._refreshAfterMutation();
        this._toast("Temporary memory deleted");
        return true;
      } catch (err) {
        this._toast(`Unable to delete temporary memory: ${err.message || String(err)}`, true);
        return false;
      }
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this, args);
      if (this._viewKey?.() !== "data-memory/memories" || this._memoryKind !== "temporary") return result;
      this.shadowRoot.querySelectorAll(".edit-temporary-memory").forEach((element) => {
        this._activate(element, () => this._openTemporaryMemory(element.dataset.id));
      });
      this.shadowRoot.querySelector("#temporary-memory-form")?.addEventListener("submit", (event) => {
        event.preventDefault();
        this._saveTemporaryMemory();
      });
      this.shadowRoot.querySelectorAll(".close-temporary-editor").forEach((button) => {
        button.addEventListener("click", () => this._closeTemporaryMemory());
      });
      this.shadowRoot.querySelector("#temporary-memory-delete")?.addEventListener("click", () => {
        const id = this._temporaryMemoryDraft?.memory_id;
        if (id) this._deleteTemporaryMemory(id);
      });
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementTemporaryMemory();
}

export {
  CATEGORY_LIMIT,
  CONTENT_LIMIT,
  ensureTemporaryScope,
  ownerLabel,
  renderTemporaryMemories,
  temporaryScopeOptions,
  validOwnerScope,
};
