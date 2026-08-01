class ExtendedOpenAIKnowledgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sources = [];
    this._query = "";
    this._editing = null;
  }

  set hass(value) {
    this._hass = value;
    if (!this._initialized) this._initialize();
  }

  set narrow(value) {
    this.toggleAttribute("narrow", Boolean(value));
  }

  connectedCallback() {
    if (this._hass && !this._initialized) this._initialize();
  }

  _call(action, data = {}) {
    return this._hass.callWS({
      type: "extended_openai_conversation_responses/knowledge",
      action,
      ...data,
    });
  }

  async _initialize() {
    this._initialized = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; color:var(--primary-text-color); background:var(--primary-background-color); }
        * { box-sizing:border-box; }
        main { width:min(1100px, calc(100% - 32px)); margin:auto; padding:28px 0 48px; }
        h1 { margin:0; font-size:28px; font-weight:500; }
        .subtitle { color:var(--secondary-text-color); line-height:1.5; max-width:760px; margin:7px 0 24px; }
        .card, .source, dialog { background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:12px; }
        .card { padding:18px; margin-bottom:22px; }
        label { display:block; color:var(--secondary-text-color); font-size:13px; font-weight:500; margin-bottom:7px; }
        select, input, textarea, button { font:inherit; }
        select, input, textarea { width:100%; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:8px; padding:10px 12px; }
        select, input { min-height:42px; }
        textarea { min-height:45vh; max-height:65vh; resize:vertical; line-height:1.5; font-family:var(--code-font-family, ui-monospace, monospace); }
        button { min-height:40px; border-radius:8px; padding:0 16px; cursor:pointer; }
        .primary { color:var(--text-primary-color, #fff); background:var(--primary-color); border:1px solid var(--primary-color); }
        .secondary { color:var(--primary-text-color); background:transparent; border:1px solid var(--divider-color); }
        .danger { color:#fff; background:var(--error-color); border:1px solid var(--error-color); }
        button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
        .agent-row, .heading, .actions, .metadata { display:flex; align-items:center; gap:10px; }
        .agent-row select { flex:1; }
        .heading { justify-content:space-between; margin-bottom:14px; }
        .heading h2 { margin:0; font-size:19px; font-weight:500; }
        #search { margin-bottom:14px; }
        #sources { display:grid; gap:10px; }
        .source { padding:17px 18px; display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:16px; }
        .source h3 { margin:0 0 7px; font-size:17px; font-weight:500; }
        .description { margin:0; color:var(--secondary-text-color); line-height:1.45; overflow-wrap:anywhere; }
        .metadata { flex-wrap:wrap; color:var(--secondary-text-color); font-size:12px; margin-top:12px; }
        .badge { background:var(--secondary-background-color); border-radius:999px; padding:5px 9px; }
        .empty { text-align:center; color:var(--secondary-text-color); padding:44px 20px; }
        .status { display:none; margin-bottom:16px; padding:11px 13px; background:var(--secondary-background-color); border-radius:8px; }
        .status.visible { display:block; }
        .status.error { color:var(--error-color); }
        dialog { width:min(900px, calc(100vw - 32px)); max-height:calc(100vh - 32px); padding:0; color:var(--primary-text-color); box-shadow:0 12px 40px rgba(0,0,0,.28); }
        dialog::backdrop { background:rgba(0,0,0,.45); }
        .dialog-title { padding:20px 22px 8px; font-size:20px; font-weight:500; }
        .dialog-body { padding:10px 22px 20px; overflow:auto; max-height:calc(100vh - 150px); }
        .field + .field { margin-top:14px; }
        .counter { margin-top:5px; text-align:right; color:var(--secondary-text-color); font-size:12px; }
        .dialog-actions { display:flex; justify-content:flex-end; gap:8px; padding:12px 22px 18px; border-top:1px solid var(--divider-color); }
        .dialog-actions .danger { margin-right:auto; }
        @media (max-width:650px) {
          main { width:calc(100% - 24px); padding-top:18px; }
          .heading, .agent-row { align-items:stretch; flex-direction:column; }
          .source { grid-template-columns:1fr; }
          .source .actions { justify-content:flex-end; }
          textarea { min-height:50vh; }
        }
      </style>
      <main>
        <h1>Knowledge Library</h1>
        <p class="subtitle">Maintain larger reference material for on-demand model search. Sources are stored locally per conversation agent and are not added to every prompt.</p>
        <section class="card">
          <label for="agent">Conversation agent</label>
          <div class="agent-row">
            <select id="agent"></select>
            <button class="secondary" id="refresh" type="button">Refresh</button>
          </div>
        </section>
        <div id="status" class="status" role="status" aria-live="polite"></div>
        <section>
          <div class="heading"><h2 id="count">Knowledge sources</h2><button class="primary" id="add" type="button">+ Add Knowledge source</button></div>
          <input id="search" type="search" placeholder="Filter by title or description" aria-label="Filter knowledge sources">
          <div id="sources"></div>
        </section>
      </main>
      <dialog id="editor" aria-labelledby="editorTitle">
        <form id="form">
          <div class="dialog-title" id="editorTitle">Add Knowledge source</div>
          <div class="dialog-body">
            <div class="field"><label for="title">Title</label><input id="title" maxlength="120" required></div>
            <div class="field"><label for="description">Description</label><input id="description" maxlength="500"></div>
            <div class="field"><label for="content">Content</label><textarea id="content" maxlength="100000" required spellcheck="true"></textarea><div class="counter" id="counter">0 / 100,000 characters</div></div>
          </div>
          <div class="dialog-actions">
            <button class="danger" id="delete" type="button" hidden>Delete</button>
            <button class="secondary" id="cancel" type="button">Cancel</button>
            <button class="primary" id="save" type="submit">Save</button>
          </div>
        </form>
      </dialog>
      <dialog id="confirm" aria-labelledby="confirmTitle">
        <div class="dialog-title" id="confirmTitle">Delete Knowledge source?</div>
        <div class="dialog-body">This permanently removes the selected source from this agent's local Knowledge Library.</div>
        <div class="dialog-actions"><button class="secondary" id="keep" type="button">Cancel</button><button class="danger" id="confirmDelete" type="button">Delete</button></div>
      </dialog>`;
    this._bind();
    await this._loadAgents();
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelector("#agent").addEventListener("change", () => this._loadSources());
    root.querySelector("#refresh").addEventListener("click", () => this._loadSources());
    root.querySelector("#add").addEventListener("click", () => this._openEditor());
    root.querySelector("#search").addEventListener("input", (event) => {
      this._query = event.target.value.trim().toLocaleLowerCase();
      this._render();
    });
    root.querySelector("#content").addEventListener("input", () => this._updateCounter());
    root.querySelector("#cancel").addEventListener("click", () => root.querySelector("#editor").close());
    root.querySelector("#form").addEventListener("submit", (event) => { event.preventDefault(); this._save(); });
    root.querySelector("#delete").addEventListener("click", () => root.querySelector("#confirm").showModal());
    root.querySelector("#keep").addEventListener("click", () => root.querySelector("#confirm").close());
    root.querySelector("#confirmDelete").addEventListener("click", () => this._delete());
  }

  _agentData() {
    const selected = this.shadowRoot.querySelector("#agent").selectedOptions[0];
    return selected ? { entry_id: selected.dataset.entry, subentry_id: selected.value } : null;
  }

  async _loadAgents() {
    try {
      const response = await this._call("agents");
      const select = this.shadowRoot.querySelector("#agent");
      select.innerHTML = "";
      for (const agent of response.agents) {
        const option = document.createElement("option");
        option.value = agent.subentry_id;
        option.dataset.entry = agent.entry_id;
        option.textContent = `${agent.title} — ${agent.entry_title}${agent.knowledge_enabled ? "" : " (Knowledge Library off)"}`;
        select.appendChild(option);
      }
      if (response.agents.length) await this._loadSources();
      else this._showStatus("No conversation agents are configured.", true);
    } catch (error) { this._showStatus(error.message || String(error), true); }
  }

  async _loadSources() {
    const agent = this._agentData();
    if (!agent) return;
    try {
      const response = await this._call("list", agent);
      this._sources = response.sources;
      this._render();
      this._showStatus("");
    } catch (error) { this._showStatus(error.message || String(error), true); }
  }

  _render() {
    const root = this.shadowRoot;
    const visible = this._sources.filter((source) => `${source.title} ${source.description}`.toLocaleLowerCase().includes(this._query));
    root.querySelector("#count").textContent = `${this._sources.length} Knowledge source${this._sources.length === 1 ? "" : "s"}`;
    const list = root.querySelector("#sources");
    list.innerHTML = "";
    if (!visible.length) {
      const empty = document.createElement("div");
      empty.className = "card empty";
      empty.textContent = this._sources.length ? "No sources match this filter." : "No Knowledge sources yet. Add one to make reference information available on demand.";
      list.appendChild(empty);
      return;
    }
    for (const source of visible) {
      const card = document.createElement("article");
      card.className = "source";
      const body = document.createElement("div");
      const title = document.createElement("h3");
      title.textContent = source.title;
      const description = document.createElement("p");
      description.className = "description";
      description.textContent = source.description || "No description";
      const metadata = document.createElement("div");
      metadata.className = "metadata";
      const size = document.createElement("span");
      size.className = "badge";
      size.textContent = `${Number(source.character_count).toLocaleString()} characters`;
      const updated = document.createElement("span");
      updated.textContent = `Updated ${this._formatDate(source.updated_at)}`;
      metadata.append(size, updated);
      body.append(title, description, metadata);
      const actions = document.createElement("div");
      actions.className = "actions";
      const edit = document.createElement("button");
      edit.className = "secondary";
      edit.type = "button";
      edit.textContent = "Edit";
      edit.addEventListener("click", () => this._openEditor(source.source_id));
      actions.appendChild(edit);
      card.append(body, actions);
      list.appendChild(card);
    }
  }

  async _openEditor(sourceId = null) {
    const root = this.shadowRoot;
    this._editing = null;
    root.querySelector("#title").value = "";
    root.querySelector("#description").value = "";
    root.querySelector("#content").value = "";
    root.querySelector("#delete").hidden = true;
    root.querySelector("#editorTitle").textContent = "Add Knowledge source";
    if (sourceId) {
      try {
        const response = await this._call("get", { ...this._agentData(), source_id: sourceId });
        this._editing = response.source;
        root.querySelector("#title").value = response.source.title;
        root.querySelector("#description").value = response.source.description;
        root.querySelector("#content").value = response.source.content;
        root.querySelector("#delete").hidden = false;
        root.querySelector("#editorTitle").textContent = "Edit Knowledge source";
      } catch (error) { this._showStatus(error.message || String(error), true); return; }
    }
    this._updateCounter();
    root.querySelector("#editor").showModal();
    root.querySelector("#title").focus();
  }

  async _save() {
    const root = this.shadowRoot;
    const data = {
      ...this._agentData(),
      title: root.querySelector("#title").value,
      description: root.querySelector("#description").value,
      content: root.querySelector("#content").value,
    };
    if (this._editing) data.source_id = this._editing.source_id;
    try {
      await this._call(this._editing ? "update" : "create", data);
      root.querySelector("#editor").close();
      await this._loadSources();
    } catch (error) { this._showStatus(error.message || String(error), true); }
  }

  async _delete() {
    if (!this._editing) return;
    try {
      await this._call("delete", { ...this._agentData(), source_id: this._editing.source_id, confirm: true });
      this.shadowRoot.querySelector("#confirm").close();
      this.shadowRoot.querySelector("#editor").close();
      await this._loadSources();
    } catch (error) { this.shadowRoot.querySelector("#confirm").close(); this._showStatus(error.message || String(error), true); }
  }

  _updateCounter() {
    const length = this.shadowRoot.querySelector("#content").value.length;
    this.shadowRoot.querySelector("#counter").textContent = `${length.toLocaleString()} / 100,000 characters`;
  }

  _formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  _showStatus(message, error = false) {
    const status = this.shadowRoot.querySelector("#status");
    status.textContent = message;
    status.className = `status${message ? " visible" : ""}${error ? " error" : ""}`;
  }
}

customElements.define("extended-openai-knowledge-panel", ExtendedOpenAIKnowledgePanel);
