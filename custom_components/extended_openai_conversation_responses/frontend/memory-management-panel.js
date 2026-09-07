import "./memory-panel.js";

const BaseMemoryPanel = customElements.get("extended-openai-memory-panel");
const PAGE_SIZE = 100;
const MAX_PAGES = 101;

class ExtendedOpenAIMemoryManagementPanel extends BaseMemoryPanel {
  _openMemoryDialog(memory = null) {
    super._openMemoryDialog(memory);
    const scope = this.shadowRoot.querySelector("#memoryScope");
    const household = scope?.querySelector('option[value="household"]');
    const sharedEnabled = Boolean(this._selected()?.shared_memory_enabled);
    if (household) household.disabled = !sharedEnabled;
    if (scope && !sharedEnabled && scope.value === "household") scope.value = "personal";
    const meta = this.shadowRoot.querySelector("#memoryMeta");
    if (meta) meta.style.removeProperty("color");
  }

  async _loadMemories() {
    if (!this._selected()) return;
    try {
      this._status("");
      const memories = [];
      let temporaryMemories = [];
      let offset = 0;
      let pages = 0;
      let complete = false;

      while (pages < MAX_PAGES) {
        const result = await this._call("list", this._data({limit: PAGE_SIZE, offset}));
        const page = Array.isArray(result.memories) ? result.memories : [];
        memories.push(...page);
        if (pages === 0) {
          temporaryMemories = Array.isArray(result.temporary_memories) ? result.temporary_memories : [];
        }
        pages += 1;
        if (result.next_offset == null) {
          complete = true;
          break;
        }
        if (!Number.isInteger(result.next_offset) || result.next_offset <= offset) {
          throw new Error("Memory paging returned an invalid continuation offset.");
        }
        offset = result.next_offset;
      }

      if (!complete) {
        throw new Error("Memory paging exceeded the supported collection size.");
      }

      this._memories = memories;
      this._temporaryMemories = temporaryMemories;
      if (this._activeCategory !== "all" && !this._memories.some((memory) => memory.category === this._activeCategory)) {
        this._activeCategory = "all";
      }
      this._renderCategories();
      this._renderMemories();
      this._renderTemporaryMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _saveDialogMemory() {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#memoryDialog");
    const content = root.querySelector("#memoryContent").value.trim();
    const category = root.querySelector("#memoryCategory").value.trim() || "general";
    const importance = root.querySelector("#memoryImportance").value;
    const scope = root.querySelector("#memoryScope").value;
    const subject = root.querySelector("#memorySubject").value.trim();
    const key = root.querySelector("#memoryKey").value.trim();
    const validFrom = root.querySelector("#memoryValidFrom").value.trim();
    const save = root.querySelector("#dialogSave");
    const meta = root.querySelector("#memoryMeta");

    if (!content) {
      meta.textContent = "Memory content cannot be blank.";
      meta.style.color = "var(--error-color)";
      root.querySelector("#memoryContent").focus();
      return;
    }

    save.disabled = true;
    try {
      if (this._editingMemory) {
        const payload = {
          memory_id: this._editingMemory.memory_id,
          content,
          category,
          importance,
          scope,
          expected_revision: this._editingMemory.revision,
        };
        const clearFields = [];
        for (const [field, value] of Object.entries({subject, key, valid_from: validFrom})) {
          if (value) payload[field] = value;
          else if (this._editingMemory[field]) clearFields.push(field);
        }
        if (clearFields.length) payload.clear_fields = clearFields;
        await this._call("update", this._data(payload));
      } else {
        const payload = {content, category, importance, scope};
        if (subject) payload.subject = subject;
        if (key) payload.key = key;
        if (validFrom) payload.valid_from = validFrom;
        await this._call("add", this._data(payload));
      }
      dialog.close();
      this._editingMemory = null;
      await this._loadMemories();
    } catch (err) {
      const message = err.message || String(err);
      meta.textContent = message;
      meta.style.color = "var(--error-color)";
      this._status(message, true);
    } finally {
      save.disabled = false;
    }
  }

  _bulkScopes() {
    const sharedEnabled = Boolean(this._selected()?.shared_memory_enabled);
    if (this._scopeFilter === "Personal") return [{scope: "personal", label: "personal"}];
    if (this._scopeFilter === "Shared household") {
      return sharedEnabled ? [{scope: "household", label: "shared household"}] : [];
    }
    return [
      {scope: "personal", label: "personal"},
      ...(sharedEnabled ? [{scope: "household", label: "shared household"}] : []),
    ];
  }

  _bulkScopeLabel(scopes) {
    if (scopes.length === 2) return "personal and shared household";
    return scopes[0]?.label || "selected";
  }

  async _clearCategory() {
    const category = this._activeCategory;
    if (!category || category === "all") return;
    const scopes = this._bulkScopes();
    if (!scopes.length) return;
    const label = this._bulkScopeLabel(scopes);
    const confirmed = await this._confirm(
      `Clear “${category}”?`,
      `Every ${label} memory in the “${category}” category will be permanently removed, regardless of the current search or importance filter.`,
      "Clear category"
    );
    if (!confirmed) return;

    try {
      for (const target of scopes) {
        await this._call("clear", this._data({category, scope: target.scope, confirm: true}));
      }
      this._activeCategory = "all";
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _clearAll() {
    if (!this._memories.length) return;
    const scopes = this._bulkScopes();
    if (!scopes.length) return;
    const label = this._bulkScopeLabel(scopes);
    const confirmed = await this._confirm(
      "Clear persistent memories?",
      `All ${label} memories for the selected conversation agent will be permanently removed. Search, category, and importance filters do not limit this action. This cannot be undone.`,
      "Clear memories"
    );
    if (!confirmed) return;

    try {
      for (const target of scopes) {
        await this._call("clear", this._data({scope: target.scope, confirm: true}));
      }
      this._activeCategory = "all";
      this._query = "";
      this.shadowRoot.querySelector("#search").value = "";
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _deleteDialogMemory() {
    if (!this._editingMemory) return;
    const memory = this._editingMemory;
    const confirmed = await this._confirm(
      "Delete memory?",
      `This ${memory.scope === "Shared household" ? "shared household" : "personal"} memory will be permanently removed.`,
      "Delete"
    );
    if (!confirmed) return;

    try {
      await this._call("delete", this._data({memory_id: memory.memory_id}));
      this.shadowRoot.querySelector("#memoryDialog").close();
      this._editingMemory = null;
      await this._loadMemories();
    } catch (err) {
      const message = err.message || String(err);
      const meta = this.shadowRoot.querySelector("#memoryMeta");
      meta.textContent = message;
      meta.style.color = "var(--error-color)";
      this._status(message, true);
    }
  }
}

if (!customElements.get("extended-openai-memory-management-panel")) {
  customElements.define("extended-openai-memory-management-panel", ExtendedOpenAIMemoryManagementPanel);
}
