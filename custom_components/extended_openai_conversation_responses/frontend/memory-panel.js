class ExtendedOpenAIMemoryPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._memories = [];
    this._temporaryMemories = [];
    this._activeCategory = "all";
    this._query = "";
    this._importanceFilter = "all";
    this._scopeFilter = "all";
    this._narrow = false;
    this._confirmResolver = null;
  }

  set hass(value) {
    this._hass = value;
    if (!this._initialized) this._initialize();
  }

  set narrow(value) {
    this._narrow = Boolean(value);
    this.toggleAttribute("narrow", this._narrow);
  }

  connectedCallback() {
    if (this._hass && !this._initialized) this._initialize();
  }

  async _call(action, data = {}) {
    return this._hass.callWS({
      type: "extended_openai_conversation_responses/manage",
      action,
      ...data,
    });
  }

  async _initialize() {
    this._initialized = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          box-sizing: border-box;
          min-height: 100%;
          color: var(--primary-text-color);
          background: var(--primary-background-color);
        }

        * { box-sizing: border-box; }

        .page {
          width: min(100% - 32px, 980px);
          margin: 0 auto;
          padding: 28px 0 48px;
        }

        .page-header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 20px;
          margin-bottom: 24px;
        }

        h1 {
          margin: 0;
          font-size: 28px;
          line-height: 1.2;
          font-weight: 500;
        }

        .subtitle {
          margin: 7px 0 0;
          color: var(--secondary-text-color);
          line-height: 1.5;
          max-width: 720px;
        }

        button, input, select, textarea {
          font: inherit;
        }

        button {
          cursor: pointer;
        }

        button:focus-visible,
        input:focus-visible,
        select:focus-visible,
        textarea:focus-visible,
        summary:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 2px;
        }

        .primary-button,
        .secondary-button,
        .danger-button,
        .icon-button,
        .chip {
          border-radius: 8px;
          min-height: 40px;
          transition: background 120ms ease, border-color 120ms ease;
        }

        .primary-button {
          border: 1px solid var(--primary-color);
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
          padding: 0 16px;
          font-weight: 500;
        }

        .secondary-button {
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          padding: 0 16px;
        }

        .secondary-button:disabled {
          opacity: .45;
          cursor: default;
        }

        .danger-button {
          border: 1px solid var(--error-color);
          background: var(--error-color);
          color: #fff;
          padding: 0 16px;
          font-weight: 500;
        }

        .icon-button {
          width: 40px;
          padding: 0;
          border: 1px solid transparent;
          background: transparent;
          color: var(--secondary-text-color);
          font-size: 20px;
          display: inline-grid;
          place-items: center;
        }

        .icon-button:hover,
        .secondary-button:hover:not(:disabled) {
          background: var(--secondary-background-color);
        }

        .icon-button.danger {
          color: var(--error-color);
        }

        .agent-card,
        .memory-card,
        .empty-state,
        .test-result {
          border: 1px solid var(--divider-color);
          border-radius: 12px;
          background: var(--card-background-color);
          box-shadow: var(--ha-card-box-shadow, none);
        }

        .agent-card {
          padding: 18px;
          margin-bottom: 22px;
        }

        .field-label {
          display: block;
          margin-bottom: 7px;
          color: var(--secondary-text-color);
          font-size: 13px;
          font-weight: 500;
        }

        .agent-row {
          display: flex;
          gap: 12px;
          align-items: center;
        }

        select,
        input[type="text"],
        input[type="search"],
        textarea {
          width: 100%;
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          padding: 10px 12px;
        }

        select,
        input[type="text"],
        input[type="search"] {
          min-height: 42px;
        }

        textarea {
          min-height: 128px;
          resize: vertical;
          line-height: 1.5;
        }

        select {
          flex: 1;
        }

        .menu {
          position: relative;
          flex: 0 0 auto;
        }

        .menu summary {
          list-style: none;
          width: 42px;
          height: 42px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          cursor: pointer;
          color: var(--secondary-text-color);
          font-size: 22px;
          user-select: none;
        }

        .menu summary::-webkit-details-marker { display: none; }
        .menu summary:hover { background: var(--secondary-background-color); }

        .menu-panel {
          position: absolute;
          z-index: 10;
          right: 0;
          top: 48px;
          min-width: 230px;
          padding: 6px;
          border: 1px solid var(--divider-color);
          border-radius: 10px;
          background: var(--card-background-color);
          box-shadow: var(--ha-card-box-shadow, 0 4px 16px rgba(0,0,0,.16));
        }

        .menu-panel button {
          width: 100%;
          border: 0;
          border-radius: 7px;
          padding: 10px 12px;
          text-align: left;
          background: transparent;
          color: var(--primary-text-color);
        }

        .menu-panel button:hover { background: var(--secondary-background-color); }
        .menu-panel button.danger { color: var(--error-color); }
        .menu-panel button:disabled { opacity: .45; cursor: default; }
        .menu-divider { height: 1px; background: var(--divider-color); margin: 5px 0; }

        .memory-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 14px;
        }

        .memory-count {
          font-size: 18px;
          font-weight: 500;
        }

        .search-wrap {
          position: relative;
          margin-bottom: 14px;
        }

        .search-wrap input {
          padding-left: 38px;
        }

        .search-icon {
          position: absolute;
          left: 13px;
          top: 50%;
          transform: translateY(-50%);
          color: var(--secondary-text-color);
          pointer-events: none;
        }

        .chips {
          display: flex;
          align-items: center;
          gap: 8px;
          overflow-x: auto;
          padding: 2px 1px 10px;
          margin-bottom: 6px;
          scrollbar-width: thin;
        }

        .chip {
          flex: 0 0 auto;
          min-height: 34px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          padding: 0 12px;
        }

        .chip[aria-pressed="true"] {
          border-color: var(--primary-color);
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
        }

        .memory-list {
          display: grid;
          gap: 10px;
        }

        .temporary-section {
          margin-top: 34px;
        }

        .section-copy {
          margin: 5px 0 0;
          color: var(--secondary-text-color);
          line-height: 1.45;
          font-size: 13px;
          max-width: 700px;
        }

        .memory-card {
          padding: 16px 18px;
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 14px;
        }

        .memory-content {
          margin: 0;
          line-height: 1.55;
          white-space: pre-wrap;
          overflow-wrap: anywhere;
        }

        .metadata {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 7px;
          margin-top: 12px;
          color: var(--secondary-text-color);
          font-size: 13px;
        }

        .badge {
          display: inline-flex;
          align-items: center;
          min-height: 26px;
          padding: 0 9px;
          border-radius: 999px;
          background: var(--secondary-background-color);
          color: var(--primary-text-color);
          font-size: 12px;
          font-weight: 500;
        }

        .memory-actions {
          display: flex;
          align-items: flex-start;
          gap: 2px;
        }

        .empty-state {
          text-align: center;
          padding: 42px 24px;
          color: var(--secondary-text-color);
        }

        .empty-state strong {
          display: block;
          margin-bottom: 8px;
          color: var(--primary-text-color);
          font-size: 18px;
          font-weight: 500;
        }

        .empty-state .primary-button {
          margin-top: 18px;
        }

        .status {
          display: none;
          margin: 0 0 18px;
          padding: 11px 13px;
          border-radius: 8px;
          background: var(--secondary-background-color);
          line-height: 1.4;
        }

        .status.visible { display: block; }
        .status.error {
          color: var(--error-color);
          border: 1px solid color-mix(in srgb, var(--error-color) 40%, transparent);
        }

        dialog {
          width: min(560px, calc(100vw - 32px));
          max-height: min(760px, calc(100vh - 32px));
          padding: 0;
          border: 0;
          border-radius: 14px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
          box-shadow: 0 12px 40px rgba(0,0,0,.28);
        }

        dialog::backdrop {
          background: rgba(0, 0, 0, .42);
        }

        .dialog-header {
          padding: 20px 22px 12px;
          font-size: 20px;
          font-weight: 500;
        }

        .dialog-body {
          padding: 8px 22px 20px;
          overflow-y: auto;
        }

        .dialog-field + .dialog-field { margin-top: 16px; }

        .dialog-meta {
          margin-top: 16px;
          color: var(--secondary-text-color);
          font-size: 13px;
          line-height: 1.5;
        }

        .dialog-actions {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          padding: 12px 22px 18px;
          border-top: 1px solid var(--divider-color);
        }

        .dialog-actions .danger-button:first-child {
          margin-right: auto;
        }

        .confirm-copy {
          margin: 0;
          line-height: 1.5;
          color: var(--secondary-text-color);
        }

        .test-result {
          margin-top: 14px;
          padding: 14px;
          white-space: pre-wrap;
          line-height: 1.5;
          font-family: var(--paper-font-body1_-_font-family, inherit);
        }

        @media (max-width: 700px) {
          .page {
            width: calc(100% - 24px);
            padding-top: 18px;
          }

          h1 { font-size: 24px; }

          .page-header,
          .memory-header,
          .agent-row {
            align-items: stretch;
          }

          .page-header { flex-direction: column; }
          .memory-header { flex-wrap: wrap; }
          .memory-header .primary-button,
          .temporary-section .secondary-button { width: 100%; }

          .agent-row select { min-width: 0; }

          .memory-card {
            grid-template-columns: minmax(0, 1fr);
          }

          .memory-actions {
            justify-content: flex-end;
            margin-top: -4px;
          }
        }
      </style>

      <main class="page">
        <header class="page-header">
          <div>
            <h1>OpenAI memories</h1>
            <p class="subtitle">Manage persistent personal and household memories, plus short-lived temporary context associated directly with your Home Assistant user.</p>
          </div>
        </header>

        <section class="agent-card" aria-label="Conversation agent">
          <span class="field-label">Conversation agent</span>
          <div class="agent-row">
            <select id="agent" aria-label="Conversation agent"></select>
            <details class="menu" id="toolsMenu">
              <summary aria-label="More actions" title="More actions">⋮</summary>
              <div class="menu-panel">
                <button type="button" id="refresh">Refresh memories</button>
                <button type="button" id="testButton">Test agent</button>
                <div class="menu-divider"></div>
                <button type="button" class="danger" id="clearCategoryButton">Clear selected category…</button>
                <button type="button" class="danger" id="clearAllButton">Clear all persistent memories…</button>
              </div>
            </details>
          </div>
        </section>

        <div id="status" class="status" role="status" aria-live="polite"></div>

        <section aria-labelledby="memoryHeading">
          <div class="memory-header">
            <div id="memoryHeading" class="memory-count">Memories</div>
            <button type="button" class="primary-button" id="addButton">+ Add memory</button>
          </div>

          <div class="search-wrap">
            <span class="search-icon" aria-hidden="true">⌕</span>
            <input id="search" type="search" placeholder="Search memories" aria-label="Search memories">
          </div>

          <div id="categories" class="chips" aria-label="Filter by category"></div>
          <div class="agent-row" aria-label="Additional filters">
            <select id="importanceFilter" aria-label="Filter by importance"><option value="all">All importance</option><option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option></select>
            <select id="scopeFilter" aria-label="Filter by scope"><option value="all">All scopes</option><option value="Personal">Personal</option><option value="Shared household">Shared household</option></select>
          </div>
          <div id="memories" class="memory-list"></div>
        </section>

        <section class="temporary-section" aria-labelledby="temporaryMemoryHeading">
          <div class="memory-header">
            <div>
              <div id="temporaryMemoryHeading" class="memory-count">Temporary memory</div>
              <p class="section-copy">Only active temporary memories associated directly with your authenticated Home Assistant user are shown here. Device- and conversation-scoped context is intentionally not exposed.</p>
            </div>
            <button type="button" class="secondary-button" id="clearTemporaryButton">Clear temporary memory</button>
          </div>
          <div id="temporaryMemories" class="memory-list"></div>
        </section>
      </main>

      <dialog id="memoryDialog" aria-labelledby="memoryDialogTitle">
        <form method="dialog" id="memoryForm">
          <div class="dialog-header" id="memoryDialogTitle">Add memory</div>
          <div class="dialog-body">
            <div class="dialog-field">
              <label class="field-label" for="memoryContent">Memory</label>
              <textarea id="memoryContent" required placeholder="What should the agent remember?"></textarea>
            </div>
            <div class="dialog-field">
              <label class="field-label" for="memoryImportance">Importance</label>
              <select id="memoryImportance"><option value="low">Low</option><option value="normal" selected>Normal</option><option value="high">High</option></select>
            </div>
            <div class="dialog-field">
              <label class="field-label" for="memoryScope">Scope</label>
              <select id="memoryScope"><option value="personal">Personal</option><option value="household">Shared household</option></select>
            </div>
            <details>
              <summary>Advanced metadata</summary>
              <div class="dialog-field"><label class="field-label" for="memorySubject">Subject (optional)</label><input id="memorySubject" type="text"></div>
              <div class="dialog-field"><label class="field-label" for="memoryKey">Canonical key (optional)</label><input id="memoryKey" type="text" placeholder="pet.oscar.breed"></div>
              <div class="dialog-field"><label class="field-label" for="memoryValidFrom">Valid from (optional ISO 8601)</label><input id="memoryValidFrom" type="text"></div>
            </details>
            <div class="dialog-field">
              <label class="field-label" for="memoryCategory">Category</label>
              <input id="memoryCategory" type="text" list="categorySuggestions" value="general" required autocomplete="off">
              <datalist id="categorySuggestions"></datalist>
            </div>
            <div id="memoryMeta" class="dialog-meta"></div>
          </div>
          <div class="dialog-actions">
            <button type="button" class="danger-button" id="dialogDelete" hidden>Delete</button>
            <button type="button" class="secondary-button" id="dialogCancel">Cancel</button>
            <button type="submit" class="primary-button" id="dialogSave">Add memory</button>
          </div>
        </form>
      </dialog>

      <dialog id="confirmDialog" aria-labelledby="confirmTitle">
        <div class="dialog-header" id="confirmTitle">Confirm</div>
        <div class="dialog-body">
          <p class="confirm-copy" id="confirmMessage"></p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="secondary-button" id="confirmCancel">Cancel</button>
          <button type="button" class="danger-button" id="confirmAccept">Confirm</button>
        </div>
      </dialog>

      <dialog id="testDialog" aria-labelledby="testTitle">
        <div class="dialog-header" id="testTitle">Agent test</div>
        <div class="dialog-body">
          <div id="testOutput" class="test-result">Testing…</div>
        </div>
        <div class="dialog-actions">
          <button type="button" class="primary-button" id="testClose">Close</button>
        </div>
      </dialog>
    `;

    this._bind();
    await this._loadAgents();
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelector("#agent").addEventListener("change", () => {
      this._activeCategory = "all";
      this._query = "";
      root.querySelector("#search").value = "";
      this._loadMemories();
    });
    root.querySelector("#refresh").addEventListener("click", () => {
      this._closeToolsMenu();
      this._loadMemories();
    });
    root.querySelector("#addButton").addEventListener("click", () => this._openMemoryDialog());
    root.querySelector("#testButton").addEventListener("click", () => {
      this._closeToolsMenu();
      this._test();
    });
    root.querySelector("#clearCategoryButton").addEventListener("click", () => {
      this._closeToolsMenu();
      this._clearCategory();
    });
    root.querySelector("#clearAllButton").addEventListener("click", () => {
      this._closeToolsMenu();
      this._clearAll();
    });
    root.querySelector("#clearTemporaryButton").addEventListener("click", () => this._clearTemporary());
    root.querySelector("#search").addEventListener("input", (event) => {
      this._query = event.target.value.trim().toLocaleLowerCase();
      this._renderMemories();
    });
    root.querySelector("#importanceFilter").addEventListener("change", (event) => { this._importanceFilter = event.target.value; this._renderMemories(); });
    root.querySelector("#scopeFilter").addEventListener("change", (event) => { this._scopeFilter = event.target.value; this._renderMemories(); });

    root.querySelector("#memoryForm").addEventListener("submit", (event) => {
      event.preventDefault();
      this._saveDialogMemory();
    });
    root.querySelector("#dialogCancel").addEventListener("click", () => root.querySelector("#memoryDialog").close());
    root.querySelector("#dialogDelete").addEventListener("click", () => this._deleteDialogMemory());

    root.querySelector("#confirmCancel").addEventListener("click", () => this._resolveConfirm(false));
    root.querySelector("#confirmAccept").addEventListener("click", () => this._resolveConfirm(true));
    root.querySelector("#confirmDialog").addEventListener("cancel", (event) => {
      event.preventDefault();
      this._resolveConfirm(false);
    });

    root.querySelector("#testClose").addEventListener("click", () => root.querySelector("#testDialog").close());
  }

  _closeToolsMenu() {
    this.shadowRoot.querySelector("#toolsMenu").removeAttribute("open");
  }

  _selected() {
    const option = this.shadowRoot.querySelector("#agent").selectedOptions[0];
    return option ? JSON.parse(option.value) : null;
  }

  _data(extra = {}) {
    const selected = this._selected();
    if (!selected) throw new Error("Select a conversation agent first.");
    return { entry_id: selected.entry_id, subentry_id: selected.subentry_id, ...extra };
  }

  _status(text, error = false) {
    const node = this.shadowRoot.querySelector("#status");
    node.textContent = text || "";
    node.classList.toggle("visible", Boolean(text));
    node.classList.toggle("error", Boolean(error));
    node.setAttribute("role", error ? "alert" : "status");
  }

  _formatSource(source) {
    const labels = {
      explicit: "Explicit request",
      automatic: "Automatic",
      manual: "Manually added",
      imported: "Imported",
    };
    return labels[source] || (source ? source.charAt(0).toUpperCase() + source.slice(1) : "Unknown source");
  }

  _formatDate(value) {
    if (!value) return "Unknown time";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  }

  async _loadAgents() {
    try {
      const result = await this._call("agents");
      const select = this.shadowRoot.querySelector("#agent");
      select.replaceChildren();
      for (const agent of result.agents) {
        const option = document.createElement("option");
        option.value = JSON.stringify(agent);
        option.textContent = `${agent.entry_title} — ${agent.title} (${agent.memory_mode})`;
        select.append(option);
      }
      if (result.agents.length) {
        await this._loadMemories();
      } else {
        this._memories = [];
        this._temporaryMemories = [];
        this._renderMemories();
        this._renderTemporaryMemories();
        this._status("No conversation agents are configured.", true);
      }
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _loadMemories() {
    if (!this._selected()) return;
    try {
      this._status("");
      const result = await this._call("list", this._data());
      this._memories = Array.isArray(result.memories) ? result.memories : [];
      this._temporaryMemories = Array.isArray(result.temporary_memories) ? result.temporary_memories : [];
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

  _categoryCounts() {
    const counts = new Map();
    for (const memory of this._memories) {
      const category = memory.category || "general";
      counts.set(category, (counts.get(category) || 0) + 1);
    }
    return counts;
  }

  _renderCategories() {
    const container = this.shadowRoot.querySelector("#categories");
    const suggestions = this.shadowRoot.querySelector("#categorySuggestions");
    const counts = this._categoryCounts();
    const categories = [...counts.keys()].sort((a, b) => a.localeCompare(b));

    container.replaceChildren();
    suggestions.replaceChildren();

    const all = this._makeCategoryChip("all", `All ${this._memories.length}`);
    container.append(all);

    for (const category of categories) {
      container.append(this._makeCategoryChip(category, `${category} ${counts.get(category)}`));
      const option = document.createElement("option");
      option.value = category;
      suggestions.append(option);
    }

    const clearCategory = this.shadowRoot.querySelector("#clearCategoryButton");
    clearCategory.disabled = this._activeCategory === "all";
    clearCategory.textContent = this._activeCategory === "all"
      ? "Clear selected category…"
      : `Clear “${this._activeCategory}”…`;
  }

  _makeCategoryChip(category, label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(this._activeCategory === category));
    button.addEventListener("click", () => {
      this._activeCategory = category;
      this._renderCategories();
      this._renderMemories();
    });
    return button;
  }

  _visibleMemories() {
    return this._memories.filter((memory) => {
      const category = memory.category || "general";
      if (this._activeCategory !== "all" && category !== this._activeCategory) return false;
      if (this._importanceFilter !== "all" && memory.importance !== this._importanceFilter) return false;
      if (this._scopeFilter !== "all" && memory.scope !== this._scopeFilter) return false;
      if (!this._query) return true;
      return `${memory.content || ""} ${category} ${memory.subject || ""} ${memory.key || ""} ${memory.scope || ""} ${memory.importance || ""}`.toLocaleLowerCase().includes(this._query);
    });
  }

  _renderMemories() {
    const container = this.shadowRoot.querySelector("#memories");
    const heading = this.shadowRoot.querySelector("#memoryHeading");
    const visible = this._visibleMemories();

    heading.textContent = this._memories.length === 1 ? "1 memory" : `${this._memories.length} memories`;
    container.replaceChildren();

    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";

      const title = document.createElement("strong");
      const copy = document.createElement("div");

      if (!this._memories.length) {
        title.textContent = "No memories yet";
        copy.textContent = "Add a memory here, or ask the assistant to remember something when memory is enabled.";
        const add = document.createElement("button");
        add.type = "button";
        add.className = "primary-button";
        add.textContent = "+ Add memory";
        add.addEventListener("click", () => this._openMemoryDialog());
        empty.append(title, copy, add);
      } else if (this._query) {
        title.textContent = "No matching memories";
        copy.textContent = "Try a different search or category.";
        empty.append(title, copy);
      } else {
        title.textContent = `No memories in ${this._activeCategory}`;
        copy.textContent = "Memories assigned to this category will appear here.";
        empty.append(title, copy);
      }

      container.append(empty);
      return;
    }

    for (const memory of visible) {
      container.append(this._memoryCard(memory));
    }
  }

  _memoryCard(memory) {
    const card = document.createElement("article");
    card.className = "memory-card";

    const body = document.createElement("div");
    const content = document.createElement("p");
    content.className = "memory-content";
    content.textContent = memory.content || "";

    const metadata = document.createElement("div");
    metadata.className = "metadata";

    const category = document.createElement("span");
    category.className = "badge";
    category.textContent = memory.category || "general";

    const source = document.createElement("span");
    source.textContent = this._formatSource(memory.source);

    const separator = document.createElement("span");
    separator.textContent = "•";
    separator.setAttribute("aria-hidden", "true");

    const updated = document.createElement("span");
    updated.textContent = `Updated ${this._formatDate(memory.updated_at)}`;

    const importance = document.createElement("span");
    importance.textContent = `${memory.importance || "normal"} importance`;
    const scope = document.createElement("span");
    scope.textContent = memory.scope || "Personal";
    metadata.append(category, scope, importance, source, separator, updated);
    if (memory.subject || memory.key || memory.valid_from || memory.last_confirmed_at) {
      const secondary = document.createElement("div");
      secondary.className = "metadata";
      secondary.textContent = [memory.subject && `Subject: ${memory.subject}`, memory.key && `Key: ${memory.key}`, memory.valid_from && `Valid from ${this._formatDate(memory.valid_from)}`, memory.last_confirmed_at && `Confirmed ${this._formatDate(memory.last_confirmed_at)}`].filter(Boolean).join(" Â· ");
      body.append(content, metadata, secondary);
    } else {
      body.append(content, metadata);
    }

    const actions = document.createElement("div");
    actions.className = "memory-actions";

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "icon-button";
    edit.textContent = "✎";
    edit.title = "Edit memory";
    edit.setAttribute("aria-label", "Edit memory");
    edit.addEventListener("click", () => this._openMemoryDialog(memory));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button danger";
    remove.textContent = "×";
    remove.title = "Delete memory";
    remove.setAttribute("aria-label", "Delete memory");
    remove.addEventListener("click", () => this._deleteMemory(memory));

    actions.append(edit, remove);
    card.append(body, actions);
    return card;
  }

  _renderTemporaryMemories() {
    const container = this.shadowRoot.querySelector("#temporaryMemories");
    const heading = this.shadowRoot.querySelector("#temporaryMemoryHeading");
    const clear = this.shadowRoot.querySelector("#clearTemporaryButton");
    const selected = this._selected();

    heading.textContent = this._temporaryMemories.length === 1
      ? "1 temporary memory"
      : `${this._temporaryMemories.length} temporary memories`;
    clear.disabled = !this._temporaryMemories.length;
    container.replaceChildren();

    if (!this._temporaryMemories.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const title = document.createElement("strong");
      title.textContent = "No user-scoped temporary memories";
      const copy = document.createElement("div");
      copy.textContent = selected?.temporary_memory_enabled
        ? "Temporary context associated with your Home Assistant user will appear here until it expires."
        : "Temporary Memory is disabled for this agent. Any previously stored user-scoped context remains manageable until it expires.";
      empty.append(title, copy);
      container.append(empty);
      return;
    }

    for (const memory of this._temporaryMemories) {
      container.append(this._temporaryMemoryCard(memory));
    }
  }

  _temporaryMemoryCard(memory) {
    const card = document.createElement("article");
    card.className = "memory-card";

    const body = document.createElement("div");
    const content = document.createElement("p");
    content.className = "memory-content";
    content.textContent = memory.content || "";

    const metadata = document.createElement("div");
    metadata.className = "metadata";
    const category = document.createElement("span");
    category.className = "badge";
    category.textContent = memory.category || "general";
    const source = document.createElement("span");
    source.textContent = this._formatSource(memory.source);
    const expires = document.createElement("span");
    expires.textContent = `Expires ${this._formatDate(memory.expires_at)}`;
    const updated = document.createElement("span");
    updated.textContent = `Updated ${this._formatDate(memory.updated_at)}`;
    metadata.append(category, source, expires, updated);
    body.append(content, metadata);

    const actions = document.createElement("div");
    actions.className = "memory-actions";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-button danger";
    remove.textContent = "×";
    remove.title = "Delete temporary memory";
    remove.setAttribute("aria-label", "Delete temporary memory");
    remove.addEventListener("click", () => this._deleteTemporaryMemory(memory));
    actions.append(remove);

    card.append(body, actions);
    return card;
  }

  async _deleteTemporaryMemory(memory) {
    const confirmed = await this._confirm(
      "Delete temporary memory?",
      "This temporary memory will be removed from your user-scoped context for the selected agent.",
      "Delete"
    );
    if (!confirmed) return;

    try {
      await this._call("temporary_delete", this._data({ memory_id: memory.memory_id }));
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _clearTemporary() {
    if (!this._temporaryMemories.length) return;
    const confirmed = await this._confirm(
      "Clear temporary memory?",
      "All active temporary memories associated directly with your Home Assistant user for this agent will be removed. Device- and conversation-scoped context is unaffected.",
      "Clear temporary memory"
    );
    if (!confirmed) return;

    try {
      await this._call("temporary_clear", this._data({ confirm: true }));
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  _openMemoryDialog(memory = null) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#memoryDialog");
    const title = root.querySelector("#memoryDialogTitle");
    const content = root.querySelector("#memoryContent");
    const category = root.querySelector("#memoryCategory");
    const meta = root.querySelector("#memoryMeta");
    const remove = root.querySelector("#dialogDelete");
    const save = root.querySelector("#dialogSave");

    this._editingMemory = memory;
    title.textContent = memory ? "Edit memory" : "Add memory";
    content.value = memory?.content || "";
    category.value = memory?.category || (this._activeCategory !== "all" ? this._activeCategory : "general");
    root.querySelector("#memoryImportance").value = memory?.importance || "normal";
    root.querySelector("#memoryScope").value = memory?.scope === "Shared household" ? "household" : "personal";
    root.querySelector("#memoryScope").disabled = false;
    root.querySelector("#memorySubject").value = memory?.subject || "";
    root.querySelector("#memoryKey").value = memory?.key || "";
    root.querySelector("#memoryValidFrom").value = memory?.valid_from || "";
    remove.hidden = !memory;
    save.textContent = memory ? "Save" : "Add memory";

    if (memory) {
      const created = memory.created_at ? `Created ${this._formatDate(memory.created_at)}` : "";
      const updated = memory.updated_at ? `Updated ${this._formatDate(memory.updated_at)}` : "";
      const source = this._formatSource(memory.source);
      meta.textContent = [source, created, updated].filter(Boolean).join(" · ");
    } else {
      meta.textContent = "Categories help organise and filter memories. You can choose an existing category or type a new one.";
    }

    dialog.showModal();
    requestAnimationFrame(() => content.focus());
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
    const valid_from = root.querySelector("#memoryValidFrom").value.trim();
    const save = root.querySelector("#dialogSave");

    if (!content) {
      root.querySelector("#memoryContent").focus();
      return;
    }

    save.disabled = true;
    try {
      if (this._editingMemory) {
        await this._call("update", this._data({
          memory_id: this._editingMemory.memory_id,
          content,
          category,
          importance, scope, original_scope: this._editingMemory.scope === "Shared household" ? "household" : "personal", subject, key, valid_from,
        }));
      } else {
        await this._call("add", this._data({ content, category, importance, scope, subject, key, valid_from }));
      }
      dialog.close();
      this._editingMemory = null;
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    } finally {
      save.disabled = false;
    }
  }

  async _deleteDialogMemory() {
    if (!this._editingMemory) return;
    const memory = this._editingMemory;
    const dialog = this.shadowRoot.querySelector("#memoryDialog");
    dialog.close();
    this._editingMemory = null;
    await this._deleteMemory(memory);
  }

  async _deleteMemory(memory) {
    const confirmed = await this._confirm(
      "Delete memory?",
      "This memory will be permanently removed for your user and the selected conversation agent.",
      "Delete"
    );
    if (!confirmed) return;

    try {
      await this._call("delete", this._data({ memory_id: memory.memory_id, scope: memory.scope === "Shared household" ? "household" : "personal" }));
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  _confirm(title, message, confirmLabel = "Confirm") {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#confirmDialog");
    root.querySelector("#confirmTitle").textContent = title;
    root.querySelector("#confirmMessage").textContent = message;
    root.querySelector("#confirmAccept").textContent = confirmLabel;

    return new Promise((resolve) => {
      this._confirmResolver = resolve;
      dialog.showModal();
    });
  }

  _resolveConfirm(value) {
    const dialog = this.shadowRoot.querySelector("#confirmDialog");
    if (dialog.open) dialog.close();
    const resolver = this._confirmResolver;
    this._confirmResolver = null;
    if (resolver) resolver(value);
  }

  async _clearCategory() {
    const category = this._activeCategory;
    if (!category || category === "all") return;

    const confirmed = await this._confirm(
      `Clear “${category}”?`,
      `Every memory in the “${category}” category will be permanently removed for your user and this agent.`,
      "Clear category"
    );
    if (!confirmed) return;

    try {
      await this._call("clear", this._data({ category, confirm: true }));
      this._activeCategory = "all";
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _clearAll() {
    if (!this._memories.length) return;

    const confirmed = await this._confirm(
      "Clear all memories?",
      "All memories for your user and the selected conversation agent will be permanently removed. This cannot be undone.",
      "Clear all memories"
    );
    if (!confirmed) return;

    try {
      await this._call("clear", this._data({ confirm: true }));
      this._activeCategory = "all";
      this._query = "";
      this.shadowRoot.querySelector("#search").value = "";
      await this._loadMemories();
    } catch (err) {
      this._status(err.message || String(err), true);
    }
  }

  async _test() {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#testDialog");
    const output = root.querySelector("#testOutput");
    output.textContent = "Testing…";
    dialog.showModal();

    try {
      const result = await this._call("test_agent", this._data());
      const checks = Array.isArray(result.checks) ? result.checks : [];
      output.textContent = [
        `Overall: ${result.status}`,
        ...checks.map((check) => `${check.name}: ${check.status} — ${check.message}`),
      ].join("\n");
    } catch (err) {
      output.textContent = err.message || String(err);
    }
  }
}

customElements.define("extended-openai-memory-panel", ExtendedOpenAIMemoryPanel);