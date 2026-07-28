class ExtendedOpenAIMemoryPanel extends HTMLElement {
  set hass(value) {
    this._hass = value;
    if (!this._initialized) this._initialize();
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
    this.innerHTML = `
      <style>
        :host { display:block; padding:24px; color:var(--primary-text-color); }
        .wrap { max-width:900px; margin:auto; }
        .toolbar,.add { display:flex; gap:12px; align-items:end; flex-wrap:wrap; margin:16px 0; }
        label { display:flex; flex-direction:column; gap:6px; min-width:220px; }
        select,input,textarea,button { font:inherit; padding:10px; box-sizing:border-box; }
        select,input,textarea { color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:6px; }
        button { border:0; border-radius:6px; background:var(--primary-color); color:var(--text-primary-color); cursor:pointer; }
        button.danger { background:var(--error-color); }
        .card { margin:12px 0; padding:16px; border-radius:10px; background:var(--card-background-color); box-shadow:var(--ha-card-box-shadow); }
        .card textarea { width:100%; min-height:80px; margin:8px 0; }
        .actions { display:flex; gap:8px; flex-wrap:wrap; }
        #status,#test { white-space:pre-wrap; margin:12px 0; }
        .muted { color:var(--secondary-text-color); }
      </style>
      <div class="wrap">
        <h1>OpenAI memories</h1>
        <p class="muted">Memories shown here belong only to your Home Assistant user and the selected conversation agent.</p>
        <div class="toolbar">
          <label>Conversation agent<select id="agent"></select></label>
          <button id="refresh">Refresh</button><button id="testButton">Test agent</button>
        </div>
        <div id="status"></div><div id="test"></div>
        <div class="add">
          <label>New memory<input id="newContent" placeholder="What should the agent remember?"></label>
          <label>Category<input id="newCategory" value="general"></label>
          <button id="addButton">Add memory</button>
        </div>
        <div class="toolbar">
          <label>Category to clear<input id="clearCategory" placeholder="e.g. preferences"></label>
          <button class="danger" id="clearCategoryButton">Clear category</button>
          <button class="danger" id="clearAllButton">Clear all memories</button>
        </div>
        <div id="memories"></div>
      </div>`;
    this._bind();
    await this._loadAgents();
  }

  _bind() {
    this.querySelector("#agent").onchange = () => this._loadMemories();
    this.querySelector("#refresh").onclick = () => this._loadMemories();
    this.querySelector("#addButton").onclick = () => this._add();
    this.querySelector("#testButton").onclick = () => this._test();
    this.querySelector("#clearCategoryButton").onclick = () => this._clearCategory();
    this.querySelector("#clearAllButton").onclick = () => this._clearAll();
  }

  _selected() {
    const option = this.querySelector("#agent").selectedOptions[0];
    return option ? JSON.parse(option.value) : null;
  }

  _data(extra = {}) {
    const selected = this._selected();
    if (!selected) throw new Error("Select a conversation agent first.");
    return { entry_id:selected.entry_id, subentry_id:selected.subentry_id, ...extra };
  }

  _status(text, error = false) {
    const node = this.querySelector("#status");
    node.textContent = text || "";
    node.style.color = error ? "var(--error-color)" : "var(--primary-text-color)";
  }

  async _loadAgents() {
    try {
      const result = await this._call("agents");
      const select = this.querySelector("#agent");
      select.replaceChildren();
      for (const agent of result.agents) {
        const option = document.createElement("option");
        option.value = JSON.stringify(agent);
        option.textContent = `${agent.entry_title} — ${agent.title} (${agent.memory_mode})`;
        select.append(option);
      }
      if (result.agents.length) await this._loadMemories();
      else this._status("No conversation agents are configured.", true);
    } catch (err) { this._status(err.message || String(err), true); }
  }

  async _loadMemories() {
    const selected = this._selected();
    if (!selected) return;
    try {
      const result = await this._call("list", this._data());
      this._renderMemories(result.memories);
      this._status(`${result.memories.length} memories`);
    } catch (err) { this._status(err.message || String(err), true); }
  }

  _renderMemories(memories) {
    const container = this.querySelector("#memories");
    container.replaceChildren();
    for (const memory of memories) {
      const card = document.createElement("div"); card.className = "card";
      const category = document.createElement("input"); category.value = memory.category;
      const content = document.createElement("textarea"); content.value = memory.content;
      const metadata = document.createElement("div"); metadata.className = "muted";
      metadata.textContent = `${memory.source} • updated ${memory.updated_at}`;
      const actions = document.createElement("div"); actions.className = "actions";
      const save = document.createElement("button"); save.textContent = "Save";
      save.onclick = async () => {
        try {
          await this._call("update", this._data({memory_id:memory.memory_id, content:content.value, category:category.value}));
          await this._loadMemories();
        } catch (err) { this._status(err.message || String(err), true); }
      };
      const remove = document.createElement("button"); remove.textContent = "Delete"; remove.className = "danger";
      remove.onclick = async () => {
        if (!confirm("Delete this memory?")) return;
        try { await this._call("delete", this._data({memory_id:memory.memory_id})); await this._loadMemories(); }
        catch (err) { this._status(err.message || String(err), true); }
      };
      actions.append(save, remove); card.append(category, content, metadata, actions); container.append(card);
    }
  }

  async _add() {
    const content = this.querySelector("#newContent");
    const category = this.querySelector("#newCategory");
    try {
      await this._call("add", this._data({content:content.value, category:category.value}));
      content.value = ""; await this._loadMemories();
    } catch (err) { this._status(err.message || String(err), true); }
  }

  async _clearCategory() {
    const category = this.querySelector("#clearCategory").value.trim();
    if (!category || !confirm(`Clear every memory in category “${category}” for this agent?`)) return;
    try { await this._call("clear", this._data({category, confirm:true})); await this._loadMemories(); }
    catch (err) { this._status(err.message || String(err), true); }
  }

  async _clearAll() {
    if (!confirm("Clear ALL memories for your user and this agent? This cannot be undone.")) return;
    try { await this._call("clear", this._data({confirm:true})); await this._loadMemories(); }
    catch (err) { this._status(err.message || String(err), true); }
  }

  async _test() {
    const output = this.querySelector("#test"); output.textContent = "Testing…";
    try {
      const result = await this._call("test_agent", this._data());
      output.textContent = [`Overall: ${result.status}`, ...result.checks.map(c => `${c.name}: ${c.status} — ${c.message}`)].join("\n");
    } catch (err) { output.textContent = err.message || String(err); }
  }
}

customElements.define("extended-openai-memory-panel", ExtendedOpenAIMemoryPanel);
