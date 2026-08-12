import { bindConfiguration, bindTools, configurationDialogs, renderConfiguration, renderTools } from "./agent-config-editor.js";

const WS_TYPE = "extended_openai_conversation_responses/management";
const SECTIONS = ["overview", "configuration", "tools", "usage", "conversations", "memories", "knowledge", "diagnostics"];
const KNOWLEDGE_TITLE_LIMIT = 120;
const KNOWLEDGE_DESCRIPTION_LIMIT = 500;
const KNOWLEDGE_LIMIT = 100000;

class ExtendedOpenAIManagementPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._section = this._sectionFromPath();
    this._data = null;
    this._result = null;
    this._busy = false;
    this._query = "";
    this._showEmptyScopes = false;
    this._confirmResolver = null;
    this._configDirty = false;
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
    this._beforeUnloadHandler = (event) => {
      if (!this._configDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._loadAgents();
  }

  set route(value) {
    this._route = value;
    const section = this._sectionFromPath();
    if (section !== this._section) this._handleRouteChange(section);
    else this._render();
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._beforeUnloadHandler);
  }

  _setConfigDirty(value) {
    const dirty = Boolean(value);
    if (dirty === this._configDirty) return;
    this._configDirty = dirty;
    const method = dirty ? "addEventListener" : "removeEventListener";
    window[method]("beforeunload", this._beforeUnloadHandler);
  }

  _clearConfigDraft() {
    this._setConfigDirty(false);
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
  }

  _syncConfigDirty() {
    const baseline = this._configData;
    this._setConfigDirty(Boolean(baseline) && (
      this._draftTitle !== baseline.title ||
      JSON.stringify(this._draft) !== JSON.stringify(baseline.config)
    ));
  }

  async _handleRouteChange(section) {
    if (["configuration", "tools"].includes(this._section) && !["configuration", "tools"].includes(section)) {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your shared Configuration and Tools draft has not been saved.", "Discard");
        if (!discard) {
          history.pushState({}, "", `/extended-openai/${this._section}`);
          return;
        }
      }
      this._clearConfigDraft();
    }
    this._section = section;
    this._query = "";
    this._result = null;
    await this._loadSection();
  }

  _sectionFromPath() {
    const candidate = window.location.pathname.split("/").filter(Boolean).pop();
    return SECTIONS.includes(candidate) ? candidate : "overview";
  }

  async _call(section, action, extra = {}) {
    if (!this._hass) return null;
    const agent = this._selectedAgent();
    return this._hass.callWS({
      type: WS_TYPE,
      section,
      action,
      ...(agent ? { entry_id: agent.entry_id, subentry_id: agent.subentry_id } : {}),
      ...extra,
    });
  }

  async _loadAgents(selectedId = null) {
    try {
      this._data = await this._hass.callWS({ type: WS_TYPE, action: "agents" });
      const saved = localStorage.getItem("extended-openai-agent");
      const agents = this._data.agents || [];
      const preferred = selectedId || saved;
      this._agentId = agents.some((item) => item.subentry_id === preferred) ? preferred : agents[0]?.subentry_id;
      if (this._agentId) localStorage.setItem("extended-openai-agent", this._agentId);
      await this._loadScopes();
      await this._loadSection();
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  async _loadScopes() {
    if (!this._selectedAgent()) return;
    const response = await this._call("scopes", "catalog");
    this._data.scopes = response.scopes || [];
    const current = this._data.scopes.find((scope) => scope.is_current_user);
    if (!this._data.scopes.some((scope) => scope.scope_id === this._scopeId)) {
      this._scopeId = current?.scope_id || this._data.scopes[0]?.scope_id;
    }
  }

  _selectedAgent() {
    return this._data?.agents?.find((item) => item.subentry_id === this._agentId);
  }

  async _loadSection(silent = false) {
    if (!this._selectedAgent()) return this._render();
    if (!silent) {
      this._busy = true;
      this._render();
    }
    try {
      if (this._section === "overview") {
        const [usage, conversations, memories, knowledge] = await Promise.all([
          this._call("usage", "summary"),
          this._call("conversations", "settings", { scope_id: this._scopeId }),
          this._call("memories", "list", { scope_id: this._scopeId, limit: 5 }),
          this._call("knowledge", "list"),
        ]);
        this._result = { usage, conversations, memories, knowledge };
      } else if (this._section === "usage") {
        const [summary, days, runs, retention] = await Promise.all([
          this._call("usage", "summary"), this._call("usage", "daily"),
          this._call("usage", "runs", { limit: 30 }), this._call("usage", "retention"),
        ]);
        this._result = { summary, days, runs, retention };
      } else if (this._section === "conversations") {
        const [sessions, settings] = await Promise.all([
          this._call("conversations", "list", { scope_id: this._scopeId, limit: 50 }),
          this._call("conversations", "settings", { scope_id: this._scopeId }),
        ]);
        this._result = { sessions, settings };
      } else if (this._section === "memories") {
        this._result = await this._call("memories", "list", { scope_id: this._scopeId, limit: 100 });
      } else if (this._section === "knowledge") {
        this._result = await this._call("knowledge", "list");
      } else if (["configuration", "tools"].includes(this._section)) {
        if (!this._configData || this._draftAgentId !== this._agentId) {
          this._configData = await this._call("configuration", "get");
          this._draft = JSON.parse(JSON.stringify(this._configData.config));
          this._draftTitle = this._configData.title;
          this._draftAgentId = this._agentId;
          this._setConfigDirty(false);
        }
        this._result = this._configData;
      } else {
        this._result = null;
      }
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _navigate(section) {
    if (["configuration", "tools"].includes(this._section) && !["configuration", "tools"].includes(section)) {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your shared Configuration and Tools draft has not been saved.", "Discard");
        if (!discard) return;
      }
      this._clearConfigDraft();
    }
    this._section = section;
    this._query = "";
    history.pushState({}, "", `/extended-openai/${section}`);
    this._result = null;
    this._loadSection();
  }

  _render() {
    const agent = this._selectedAgent();
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <div class="page-shell">
        <header>
          <div class="page-heading"><h1>Extended OpenAI</h1><p>Manage conversation agents, retained data, and local knowledge.</p></div>
          <label class="agent-picker"><span>Conversation agent</span><select id="agent">${(this._data?.agents || []).map((a) => `<option value="${this._e(a.subentry_id)}" ${a.subentry_id === this._agentId ? "selected" : ""}>${this._e(a.title)}</option>`).join("")}</select>${agent ? `<small>${this._e(agent.provider)} · ${this._e(agent.model)}</small>` : ""}</label>
        </header>
        <nav aria-label="Management sections">${SECTIONS.filter((s) => this._data?.is_admin || !["configuration", "tools"].includes(s)).map((s) => `<button type="button" data-section="${s}" class="${s === this._section ? "active" : ""}">${this._label(s)}</button>`).join("")}</nav>
        ${["conversations", "memories"].includes(this._section) ? this._scopePicker() : ""}
        <main>${!agent ? this._empty("No conversation agents configured.") : this._busy ? this._loading() : this._error ? `<div class="error" role="alert">${this._e(this._error)}</div>` : this._content(agent)}</main>
      </div>
      ${this._dialogs()}
      <div id="toast" class="toast" role="status" aria-live="polite"></div>`;
    this._bindBase();
    this._bindActions();
  }

  _bindBase() {
    const root = this.shadowRoot;
    root.querySelectorAll("nav button").forEach((button) => button.addEventListener("click", () => this._navigate(button.dataset.section)));
    root.querySelector("#agent")?.addEventListener("change", async (event) => {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your shared Configuration and Tools draft has not been saved.", "Discard");
        if (!discard) { event.target.value = this._agentId; return; }
        this._clearConfigDraft();
      }
      this._agentId = event.target.value;
      localStorage.setItem("extended-openai-agent", this._agentId);
      this._scopeId = null;
      await this._loadScopes();
      await this._loadSection();
    });
    root.querySelector("#scope")?.addEventListener("change", (event) => { this._scopeId = event.target.value; this._loadSection(); });
    root.querySelector("#show-empty-scopes")?.addEventListener("change", (event) => { this._showEmptyScopes = event.target.checked; this._render(); });
    root.querySelector("#confirm-cancel")?.addEventListener("click", () => this._resolveConfirm(false));
    root.querySelector("#confirm-accept")?.addEventListener("click", () => this._resolveConfirm(true));
    root.querySelector("#confirm-dialog")?.addEventListener("cancel", (event) => { event.preventDefault(); this._resolveConfirm(false); });
  }

  _content(agent) {
    if (this._section === "overview") return this._overview(agent);
    if (this._section === "configuration") return renderConfiguration(this);
    if (this._section === "tools") return renderTools(this);
    if (this._section === "usage") return this._usage();
    if (this._section === "conversations") return this._conversations();
    if (this._section === "memories") return this._memories();
    if (this._section === "knowledge") return this._knowledge();
    return this._diagnostics(agent);
  }

  _overview(agent) {
    const usage = this._result?.usage || {};
    return `<section class="metric-grid" aria-label="Agent overview">
      ${this._metric("Provider & model", agent.provider, agent.model)}
      ${this._metric("Tokens today", usage.today?.total_tokens ?? 0)}
      ${this._metric("Tokens this month", usage.month?.total_tokens ?? 0)}
      ${this._metric("Lifetime tokens", usage.lifetime?.total_tokens ?? 0)}
      ${this._metric("Latest response", usage.latest?.total_tokens ?? "—", "tokens")}
      ${this._metric("Memories", this._titleCase(agent.memory_mode), `${agent.memory_count} memories`)}
      ${this._metric("Knowledge", agent.knowledge_enabled ? "Enabled" : "Disabled", `${agent.knowledge_source_count} sources`)}
      ${this._metric("Archive", agent.archive_enabled ? "Enabled" : "Disabled")}
    </section>`;
  }

  _usage() {
    const result = this._result || {};
    const days = result.days?.days || [];
    const max = Math.max(1, ...days.map((day) => day.total_tokens));
    return `<section class="metric-grid compact">${this._metric("Today", result.summary?.today?.total_tokens || 0)}${this._metric("This month", result.summary?.month?.total_tokens || 0)}${this._metric("Lifetime", result.summary?.lifetime?.total_tokens || 0)}${this._metric("Latest response", result.summary?.latest?.total_tokens ?? "—")}</section>
      <section class="content-card"><h2>Tokens by day</h2><div class="chart" aria-label="Daily token usage">${days.slice(-31).map((day) => `<span tabindex="0" aria-label="${this._e(day.date)}: ${day.total_tokens} tokens" title="${this._e(day.date)}: ${day.total_tokens.toLocaleString()} tokens" style="height:${Math.max(2, day.total_tokens / max * 100)}%"></span>`).join("") || this._empty("No completed runs yet.")}</div></section>
      <section class="content-card"><h2>Recent runs</h2>${this._table(["Completed", "Tokens", "Requests", "Duration", "Result"], (result.runs?.runs || []).map((run) => [run.completed_at || "—", run.total_tokens, run.request_count, `${run.duration_ms} ms`, run.successful ? "Success" : run.error_type || "Failed"]))}</section>
      ${this._data?.is_admin ? `<section class="content-card"><h2>Usage detail maintenance</h2><p>Retention is configured in the unified Configuration section.</p><div class="section-actions"><button type="button" id="clear-details" class="danger secondary-danger">Clear recent details</button></div><small>Daily, monthly, and lifetime totals are never removed by detail pruning.</small></section>` : ""}`;
  }

  _conversations() {
    const result = this._result || {};
    const settings = result.settings || {};
    return `<section class="notice ${settings.archive_enabled ? "on" : ""}"><div><strong>Archive ${settings.archive_enabled ? "enabled" : "disabled"}</strong><p>${settings.archive_retention_days || 30}-day retention · Model search ${settings.archive_model_search_enabled ? "on" : "off"}</p></div></section>
      <section class="content-card"><div class="section-heading"><div><h2>Retained conversations</h2><p>Search and review conversations for the selected scope.</p></div></div><div class="search-row"><input id="archive-query" type="search" placeholder="Search retained discussions" aria-label="Search retained discussions"><button type="button" id="archive-search">Search</button></div><div class="list">${(result.sessions?.sessions || []).map((item) => `<article class="list-card"><div class="card-main clickable open-session" tabindex="0" role="button" data-id="${this._e(item.session_id)}"><h3>${this._e(item.title || "Untitled conversation")}</h3><p class="meta">${this._e(this._formatDate(item.last_message_at))} · ${this._e(String(item.turn_count))} turns · ${this._e(item.scope_source)}</p></div><div class="actions"><button type="button" class="secondary view-session" data-id="${this._e(item.session_id)}">View</button><button type="button" class="danger delete-session" data-id="${this._e(item.session_id)}">Delete</button></div></article>`).join("") || this._empty("No retained conversations in this scope.")}</div></section>
      ${this._data?.is_admin ? `<p class="help">Archive behaviour and retention are configured in the unified Configuration section.</p>` : ""}`;
  }

  _memories() {
    const items = this._filtered(this._result?.memories || [], (item) => `${item.content} ${item.category} ${item.source}`);
    return `<section class="content-card"><div class="section-heading"><div><h2>Memories</h2><p>Lightweight facts available to this conversation agent.</p></div><button type="button" id="add-memory">+ Add memory</button></div><input id="list-search" class="search" type="search" value="${this._e(this._query)}" placeholder="Search memories" aria-label="Search memories"><div class="list memory-list">${items.map((memory) => `<article class="list-card"><div class="card-main clickable edit-memory" tabindex="0" role="button" data-id="${this._e(memory.memory_id)}"><p class="primary-copy">${this._e(memory.content)}</p><p class="meta">${this._e(memory.category)} · ${this._e(memory.source)} · Updated ${this._e(this._formatDate(memory.updated_at))}</p></div><div class="actions"><button type="button" class="secondary memory-edit-button" data-id="${this._e(memory.memory_id)}">Edit</button>${this._data?.is_admin && this._scopeId === "__anonymous__" ? `<button type="button" class="secondary reassign-memory" data-id="${this._e(memory.memory_id)}">Reassign</button>` : ""}<button type="button" class="danger delete-memory" data-id="${this._e(memory.memory_id)}">Delete</button></div></article>`).join("") || this._empty(this._query ? "No memories match this filter." : "No memories in this scope.")}</div>${this._data?.is_admin && this._scopeId === "__anonymous__" ? `<p class="help">Legacy anonymous memories remain unassigned until an administrator explicitly reassigns or deletes them.</p>` : ""}</section>`;
  }

  _knowledge() {
    const sources = this._result?.sources || [];
    const items = this._filtered(sources, (source) => `${source.title} ${source.description}`);
    return `<section class="content-card"><div class="section-heading"><div><h2>Knowledge Library</h2><p>${sources.length} source${sources.length === 1 ? "" : "s"} stored locally for on-demand search.</p></div><button type="button" id="add-source">+ Add source</button></div><input id="list-search" class="search" type="search" value="${this._e(this._query)}" placeholder="Filter by title or description" aria-label="Filter Knowledge sources"><div class="list knowledge-list">${items.map((source) => `<article class="list-card"><div class="card-main clickable edit-source" tabindex="0" role="button" data-id="${this._e(source.source_id)}"><h3>${this._e(source.title)}</h3><p class="description">${this._e(source.description || "No description")}</p><p class="meta">${Number(source.character_count || 0).toLocaleString()} characters · Updated ${this._e(this._formatDate(source.updated_at))}</p></div><div class="actions"><button type="button" class="secondary source-edit-button" data-id="${this._e(source.source_id)}">Edit</button><button type="button" class="danger delete-source" data-id="${this._e(source.source_id)}">Delete</button></div></article>`).join("") || this._empty(this._query ? "No sources match this filter." : "No Knowledge sources yet. Add one to make reference information available on demand.")}</div></section>`;
  }

  _diagnostics(agent) {
    return `<section class="metric-grid">${this._metric("Agent", agent.title)}${this._metric("Provider", agent.provider)}${this._metric("Model", agent.model)}${this._metric("Archive", agent.archive_enabled ? "Enabled" : "Disabled")}</section><section class="content-card"><h2>Safe agent test</h2><p>Tests one minimal provider response without executing Home Assistant actions.</p><button type="button" id="test-agent">Test agent</button><pre id="test-result"></pre></section>`;
  }

  _dialogs() {
    return `<dialog id="knowledge-dialog" class="editor-dialog wide" aria-labelledby="knowledge-dialog-title"><form id="knowledge-form"><div class="dialog-header"><h2 id="knowledge-dialog-title">Add Knowledge source</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Title<input id="knowledge-title" maxlength="${KNOWLEDGE_TITLE_LIMIT}" required></label><label>Description<textarea id="knowledge-description" class="short-textarea" maxlength="${KNOWLEDGE_DESCRIPTION_LIMIT}" spellcheck="true"></textarea></label><label>Content<textarea id="knowledge-content" class="knowledge-editor" maxlength="${KNOWLEDGE_LIMIT}" required spellcheck="true"></textarea></label><div id="knowledge-counter" class="counter">0 / ${KNOWLEDGE_LIMIT.toLocaleString()} characters</div><div id="knowledge-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="knowledge-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="knowledge-save">Save</button></div></form></dialog>
      <dialog id="memory-dialog" class="editor-dialog" aria-labelledby="memory-dialog-title"><form id="memory-form"><div class="dialog-header"><h2 id="memory-dialog-title">Add memory</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Memory<textarea id="memory-content" required spellcheck="true" placeholder="What should the agent remember?"></textarea></label><label>Category<input id="memory-category" value="general" required></label><p id="memory-meta" class="meta"></p><div id="memory-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="memory-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="memory-save">Save</button></div></form></dialog>
      <dialog id="session-dialog" class="editor-dialog wide" aria-labelledby="session-title"><div class="dialog-header"><h2 id="session-title">Conversation</h2><button type="button" class="icon close-session" aria-label="Close">×</button></div><div id="session-body" class="dialog-body session-body"></div><div class="dialog-actions"><button type="button" class="secondary close-session">Close</button></div></dialog>
      <dialog id="reassign-dialog" class="editor-dialog" aria-labelledby="reassign-title"><div class="dialog-header"><h2 id="reassign-title">Reassign legacy memory</h2></div><div class="dialog-body"><label>New owner<select id="reassign-scope">${this._scopeOptions("memories", true, true)}</select></label></div><div class="dialog-actions"><button type="button" class="secondary" id="reassign-cancel">Cancel</button><button type="button" id="reassign-save">Reassign</button></div></dialog>
      <dialog id="confirm-dialog" class="editor-dialog confirm-dialog" aria-labelledby="confirm-title"><div class="dialog-header"><h2 id="confirm-title">Confirm</h2></div><div class="dialog-body"><p id="confirm-message"></p></div><div class="dialog-actions"><button type="button" class="secondary" id="confirm-cancel">Cancel</button><button type="button" class="danger" id="confirm-accept">Confirm</button></div></dialog>
      ${configurationDialogs(this)}`;
  }

  _bindActions() {
    const root = this.shadowRoot;
    const q = (selector) => root.querySelector(selector);
    q("#list-search")?.addEventListener("input", (event) => { this._query = event.target.value; this._updateVisibleList(); });
    q("#add-source")?.addEventListener("click", () => this._openKnowledge());
    q("#add-memory")?.addEventListener("click", () => this._openMemory());
    root.querySelectorAll(".edit-source, .source-edit-button").forEach((element) => this._activate(element, () => this._openKnowledge(element.dataset.id)));
    root.querySelectorAll(".edit-memory, .memory-edit-button").forEach((element) => this._activate(element, () => this._openMemory(element.dataset.id)));
    root.querySelectorAll(".open-session, .view-session").forEach((element) => this._activate(element, () => this._openSession(element.dataset.id)));
    root.querySelectorAll(".delete-source").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteSource(button.dataset.id); }));
    root.querySelectorAll(".delete-memory").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteMemory(button.dataset.id); }));
    root.querySelectorAll(".delete-session").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteSession(button.dataset.id); }));
    root.querySelectorAll(".reassign-memory").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._openReassign(button.dataset.id); }));
    root.querySelectorAll(".close-editor").forEach((button) => button.addEventListener("click", () => this._requestEditorClose()));
    root.querySelectorAll(".close-session").forEach((button) => button.addEventListener("click", () => q("#session-dialog").close()));
    q("#knowledge-form")?.addEventListener("submit", (event) => { event.preventDefault(); this._saveKnowledge(); });
    q("#memory-form")?.addEventListener("submit", (event) => { event.preventDefault(); this._saveMemory(); });
    q("#knowledge-content")?.addEventListener("input", () => this._updateKnowledgeCounter());
    q("#knowledge-delete")?.addEventListener("click", () => this._deleteSource(this._editingSource?.source_id, true));
    q("#memory-delete")?.addEventListener("click", () => this._deleteMemory(this._editingMemory?.memory_id, true));
    [q("#knowledge-dialog"), q("#memory-dialog")].forEach((dialog) => dialog?.addEventListener("cancel", (event) => { event.preventDefault(); this._requestEditorClose(); }));
    q("#session-dialog")?.addEventListener("cancel", (event) => { event.preventDefault(); q("#session-dialog").close(); });
    q("#reassign-cancel")?.addEventListener("click", () => q("#reassign-dialog").close());
    q("#reassign-save")?.addEventListener("click", () => this._saveReassign());
    q("#clear-details")?.addEventListener("click", () => this._clearUsageDetails());
    q("#archive-search")?.addEventListener("click", () => this._searchArchive());
    q("#archive-query")?.addEventListener("keydown", (event) => { if (event.key === "Enter") this._searchArchive(); });
    q("#test-agent")?.addEventListener("click", () => this._testAgent());
    if (this._section === "configuration") bindConfiguration(this);
    if (this._section === "tools") bindTools(this);
  }

  _activate(element, callback) {
    element.addEventListener("click", (event) => { if (!event.target.closest("button") || event.currentTarget === event.target) callback(); });
    element.addEventListener("keydown", (event) => { if ((event.key === "Enter" || event.key === " ") && event.target === element) { event.preventDefault(); callback(); } });
  }

  _updateVisibleList() {
    const query = this._query.trim().toLocaleLowerCase();
    this.shadowRoot.querySelectorAll(".list-card").forEach((card) => {
      card.hidden = query && !card.textContent.toLocaleLowerCase().includes(query);
    });
  }

  async _openKnowledge(sourceId = null) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#knowledge-dialog");
    const loadToken = (this._knowledgeLoadToken || 0) + 1;
    this._knowledgeLoadToken = loadToken;
    this._editingSource = null;
    this._knowledgeMode = sourceId ? "edit-loading" : "create";
    this._editorInitial = null;
    this._setDialogError("knowledge", "");
    root.querySelector("#knowledge-title").value = "";
    root.querySelector("#knowledge-description").value = "";
    root.querySelector("#knowledge-content").value = "";
    root.querySelector("#knowledge-delete").hidden = true;
    root.querySelector("#knowledge-dialog-title").textContent = sourceId ? "Loading source…" : "Add Knowledge source";
    this._setKnowledgeEditorDisabled(Boolean(sourceId));
    this._editorKind = "knowledge";
    dialog.showModal();
    if (sourceId) {
      try {
        const response = await this._call("knowledge", "get", { source_id: sourceId });
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._editingSource = response.source;
        this._knowledgeMode = "edit";
        root.querySelector("#knowledge-title").value = response.source.title || "";
        root.querySelector("#knowledge-description").value = response.source.description || "";
        root.querySelector("#knowledge-content").value = response.source.content || "";
        root.querySelector("#knowledge-delete").hidden = false;
        root.querySelector("#knowledge-dialog-title").textContent = "Edit Knowledge source";
        this._setKnowledgeEditorDisabled(false);
      } catch (err) {
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._knowledgeMode = "edit-error";
        this._setDialogError("knowledge", `Unable to load source: ${err.message || String(err)}`);
        root.querySelector("#knowledge-dialog-title").textContent = "Unable to load source";
      }
    }
    if (["create", "edit"].includes(this._knowledgeMode)) {
      this._editorInitial = this._knowledgeValues();
    }
    this._updateKnowledgeCounter();
    requestAnimationFrame(() => (this._knowledgeMode === "edit-error" ? root.querySelector(".close-editor") : root.querySelector("#knowledge-title")).focus());
  }

  async _openMemory(memoryId = null) {
    const root = this.shadowRoot;
    const memory = (this._result?.memories || []).find((item) => item.memory_id === memoryId) || null;
    this._editingMemory = memory;
    this._editorKind = "memory";
    root.querySelector("#memory-dialog-title").textContent = memory ? "Edit memory" : "Add memory";
    root.querySelector("#memory-content").value = memory?.content || "";
    root.querySelector("#memory-category").value = memory?.category || "general";
    root.querySelector("#memory-delete").hidden = !memory;
    root.querySelector("#memory-meta").textContent = memory ? [memory.source, memory.created_at ? `Created ${this._formatDate(memory.created_at)}` : "", memory.updated_at ? `Updated ${this._formatDate(memory.updated_at)}` : ""].filter(Boolean).join(" · ") : "Categories help organise memories.";
    this._setDialogError("memory", "");
    this._editorInitial = this._memoryValues();
    root.querySelector("#memory-dialog").showModal();
    requestAnimationFrame(() => root.querySelector("#memory-content").focus());
  }

  async _requestEditorClose() {
    const kind = this._editorKind;
    const dialog = this.shadowRoot.querySelector(`#${kind}-dialog`);
    if (!dialog?.open) return;
    const current = kind === "knowledge" ? this._knowledgeValues() : this._memoryValues();
    if (this._editorInitial !== null && JSON.stringify(current) !== JSON.stringify(this._editorInitial)) {
      const discard = await this._confirm("Discard unsaved changes?", "Your changes have not been saved.", "Discard");
      if (!discard) return;
    }
    if (kind === "knowledge") this._knowledgeLoadToken = (this._knowledgeLoadToken || 0) + 1;
    dialog.close();
  }

  _setKnowledgeEditorDisabled(disabled) {
    const root = this.shadowRoot;
    ["#knowledge-title", "#knowledge-description", "#knowledge-content", "#knowledge-save"].forEach((selector) => {
      root.querySelector(selector).disabled = disabled;
    });
  }

  _knowledgeValues() {
    const root = this.shadowRoot;
    return { title: root.querySelector("#knowledge-title").value, description: root.querySelector("#knowledge-description").value, content: root.querySelector("#knowledge-content").value };
  }

  _memoryValues() {
    const root = this.shadowRoot;
    return { content: root.querySelector("#memory-content").value, category: root.querySelector("#memory-category").value };
  }

  async _saveKnowledge() {
    const button = this.shadowRoot.querySelector("#knowledge-save");
    if (button.disabled || !["create", "edit"].includes(this._knowledgeMode)) return;
    const values = this._knowledgeValues();
    const editing = this._knowledgeMode === "edit";
    this._setSaving(button, true);
    try {
      await this._call("knowledge", editing ? "update" : "create", { ...(editing ? { source_id: this._editingSource.source_id } : {}), ...values });
      this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast(editing ? "Knowledge source updated" : "Knowledge source saved");
    } catch (err) {
      this._setDialogError("knowledge", `Unable to save source: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _saveMemory() {
    const button = this.shadowRoot.querySelector("#memory-save");
    if (button.disabled) return;
    const values = this._memoryValues();
    this._setSaving(button, true);
    try {
      await this._call("memories", this._editingMemory ? "update" : "add", { scope_id: this._scopeId, ...(this._editingMemory ? { memory_id: this._editingMemory.memory_id } : {}), content: values.content.trim(), category: values.category.trim() || "general" });
      this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast(this._editingMemory ? "Memory updated" : "Memory added");
    } catch (err) {
      this._setDialogError("memory", `Unable to save memory: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _deleteSource(sourceId, fromDialog = false) {
    if (!sourceId || !await this._confirm("Delete Knowledge source?", "This permanently removes the selected source from this agent's local Knowledge Library.", "Delete")) return;
    try {
      await this._call("knowledge", "delete", { source_id: sourceId, confirm: true });
      if (fromDialog) this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Knowledge source deleted");
    } catch (err) { this._toast(`Unable to delete source: ${err.message || String(err)}`, true); }
  }

  async _deleteMemory(memoryId, fromDialog = false) {
    if (!memoryId || !await this._confirm("Delete memory?", "This memory will be permanently removed from the selected scope.", "Delete")) return;
    try {
      await this._call("memories", "delete", { scope_id: this._scopeId, memory_id: memoryId });
      if (fromDialog) this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Memory deleted");
    } catch (err) { this._toast(`Unable to delete memory: ${err.message || String(err)}`, true); }
  }

  async _openSession(sessionId) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#session-dialog");
    root.querySelector("#session-title").textContent = "Loading conversation…";
    root.querySelector("#session-body").innerHTML = this._loading();
    dialog.showModal();
    try {
      const data = await this._call("conversations", "get", { scope_id: this._scopeId, session_id: sessionId, limit: 100 });
      root.querySelector("#session-title").textContent = data.session.title || "Untitled conversation";
      root.querySelector("#session-body").innerHTML = data.turns.map((turn) => `<article class="turn"><div class="message user"><strong>You</strong><p>${this._e(turn.user_text)}</p></div><div class="message assistant"><strong>Assistant</strong><p>${this._e(turn.assistant_text)}</p></div><small>${this._e(this._formatDate(turn.timestamp))}</small></article>`).join("") || this._empty("No turns retained.");
    } catch (err) { root.querySelector("#session-body").innerHTML = `<div class="error" role="alert">${this._e(err.message || String(err))}</div>`; }
  }

  async _deleteSession(sessionId) {
    if (!await this._confirm("Delete conversation?", "This retained conversation and its turns will be permanently removed.", "Delete")) return;
    try { await this._call("conversations", "delete", { scope_id: this._scopeId, session_id: sessionId }); await this._refreshAfterMutation(); this._toast("Conversation deleted"); }
    catch (err) { this._toast(`Unable to delete conversation: ${err.message || String(err)}`, true); }
  }

  _openReassign(memoryId) {
    this._reassignMemoryId = memoryId;
    this.shadowRoot.querySelector("#reassign-dialog").showModal();
  }

  async _saveReassign() {
    const target = this.shadowRoot.querySelector("#reassign-scope").value;
    if (!target) return;
    try {
      const result = await this._call("memories", "reassign_legacy", { scope_id: "__anonymous__", target_scope_id: target, memory_ids: [this._reassignMemoryId] });
      this.shadowRoot.querySelector("#reassign-dialog").close();
      await this._refreshAfterMutation();
      this._toast(`Reassigned ${result.reassigned} memory record${result.reassigned === 1 ? "" : "s"}`);
    } catch (err) { this._toast(`Unable to reassign memory: ${err.message || String(err)}`, true); }
  }

  async _clearUsageDetails() {
    if (!await this._confirm("Clear recent usage details?", "Request and run details will be removed. Daily, monthly, and lifetime totals remain.", "Clear details")) return;
    try { await this._call("usage", "clear_details", { confirm: true }); await this._loadSection(); this._toast("Recent usage details cleared"); }
    catch (err) { this._toast(`Unable to clear details: ${err.message || String(err)}`, true); }
  }

  async _searchArchive() {
    const input = this.shadowRoot.querySelector("#archive-query");
    const button = this.shadowRoot.querySelector("#archive-search");
    if (button.disabled) return;
    this._setSaving(button, true, "Searching…");
    try {
      const found = await this._call("conversations", "search", { scope_id: this._scopeId, query: input.value, limit: 20 });
      this._result.sessions = { sessions: found.results.map((item) => ({ ...item, last_message_at: item.timestamp, turn_count: "matching" })) };
      this._render();
    } catch (err) { this._toast(`Unable to search: ${err.message || String(err)}`, true); }
    finally { this._setSaving(button, false); }
  }

  async _testAgent() {
    const output = this.shadowRoot.querySelector("#test-result");
    output.textContent = "Testing…";
    try { output.textContent = JSON.stringify(await this._call("diagnostics", "test_agent"), null, 2); }
    catch (err) { output.textContent = err.message || String(err); }
  }

  async _refreshAfterMutation() {
    await this._loadScopes();
    await this._loadSection(true);
  }

  _confirm(title, message, confirmLabel = "Confirm") {
    const root = this.shadowRoot;
    root.querySelector("#confirm-title").textContent = title;
    root.querySelector("#confirm-message").textContent = message;
    root.querySelector("#confirm-accept").textContent = confirmLabel;
    root.querySelector("#confirm-dialog").showModal();
    return new Promise((resolve) => { this._confirmResolver = resolve; });
  }

  _resolveConfirm(value) {
    const dialog = this.shadowRoot.querySelector("#confirm-dialog");
    if (dialog?.open) dialog.close();
    const resolver = this._confirmResolver;
    this._confirmResolver = null;
    if (resolver) resolver(value);
  }

  _setSaving(button, saving, label = "Saving…") {
    if (!button) return;
    if (saving) button.dataset.label = button.textContent;
    button.disabled = saving;
    button.textContent = saving ? label : button.dataset.label || "Save";
  }

  _setDialogError(kind, message) {
    const element = this.shadowRoot.querySelector(`#${kind}-error`);
    if (element) element.textContent = message;
  }

  _updateKnowledgeCounter() {
    const length = this.shadowRoot.querySelector("#knowledge-content")?.value.length || 0;
    this.shadowRoot.querySelector("#knowledge-counter").textContent = `${length.toLocaleString()} / ${KNOWLEDGE_LIMIT.toLocaleString()} characters`;
  }

  _toast(message, error = false) {
    const toast = this.shadowRoot.querySelector("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast visible${error ? " toast-error" : ""}`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { toast.className = "toast"; }, 5000);
  }

  _scopePicker() {
    const hasEmpty = (this._data?.scopes || []).some((scope) => (this._section === "memories" ? scope.memory_count : scope.conversation_count) === 0 && scope.scope_type === "user" && !scope.is_current_user);
    return `<section class="scope-bar"><label><span>${this._section === "memories" ? "Memory owner" : "Conversation owner"}</span><select id="scope">${this._scopeOptions(this._section)}</select></label>${hasEmpty ? `<label class="show-empty"><input id="show-empty-scopes" type="checkbox" ${this._showEmptyScopes ? "checked" : ""}> Show users with no ${this._section === "memories" ? "memories" : "conversations"}</label>` : ""}${this._data?.is_admin ? `<small>Administrator scope view</small>` : ""}</section>`;
  }

  _scopeOptions(section, includeEmpty = this._showEmptyScopes, excludeLegacy = false) {
    const key = section === "memories" ? "memory_count" : "conversation_count";
    const scopes = [...(this._data?.scopes || [])].filter((scope) => !excludeLegacy || scope.scope_type !== "anonymous_legacy");
    scopes.sort((a, b) => {
      if (a.scope_type === "anonymous_legacy") return 1;
      if (b.scope_type === "anonymous_legacy") return -1;
      if (a.is_current_user !== b.is_current_user) return a.is_current_user ? -1 : 1;
      const populated = Number(b[key] > 0) - Number(a[key] > 0);
      return populated || a.display_name.localeCompare(b.display_name);
    });
    const visible = scopes.filter((scope) => scope.scope_id === this._scopeId || scope.is_current_user || scope[key] > 0 || scope.scope_type !== "user" || includeEmpty);
    return visible.map((scope) => `<option value="${this._e(scope.scope_id)}" ${scope.scope_id === this._scopeId ? "selected" : ""}>${this._e(scope.display_name)} (${Number(scope[key] || 0).toLocaleString()})${scope.is_current_user ? " · You" : ""}</option>`).join("");
  }

  _filtered(items, value) {
    const query = this._query.trim().toLocaleLowerCase();
    return query ? items.filter((item) => value(item).toLocaleLowerCase().includes(query)) : items;
  }

  _retentionOptions(selected) { return [0,7,30,90,180,365].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value ? `${value} days` : "Disabled"}</option>`).join(""); }
  _metric(title, value, detail = "") { return `<article class="metric"><span>${this._e(title)}</span><strong>${this._e(String(value))}</strong>${detail ? `<small>${this._e(String(detail))}</small>` : ""}</article>`; }
  _toggle(id, label, checked) { return `<label class="toggle"><span>${this._e(label)}</span><input id="${id}" type="checkbox" role="switch" ${checked ? "checked" : ""}></label>`; }
  _table(headers, rows) { return `<div class="table"><table><thead><tr>${headers.map((header) => `<th>${this._e(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${this._e(String(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }
  _loading() { return `<div class="loading" role="status"><span class="spinner"></span>Loading…</div>`; }
  _empty(message) { return `<div class="empty">${this._e(message)}</div>`; }
  _label(value) { return value[0].toUpperCase() + value.slice(1); }
  _titleCase(value) { return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
  _formatDate(value) { if (!value) return "Unknown date"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(); }
  _e(value) { return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]); }

  _styles() { return `
    :host{display:block;min-height:100%;padding:28px;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui);box-sizing:border-box;background:var(--primary-background-color)}*{box-sizing:border-box}.page-shell{max-width:1220px;margin:auto}header{display:flex;justify-content:space-between;gap:36px;align-items:end;margin-bottom:28px}.page-heading h1{margin:0;font-size:30px;font-weight:500}.page-heading p,.section-heading p,.notice p{margin:6px 0 0;color:var(--secondary-text-color);line-height:1.5}.agent-picker{width:min(390px,100%)}label{display:grid;gap:7px;font-size:13px;color:var(--secondary-text-color)}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;min-height:42px;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:9px;padding:10px 12px}textarea{resize:vertical;line-height:1.55}button{min-height:42px;border:0;border-radius:9px;padding:9px 16px;cursor:pointer;background:var(--primary-color);color:var(--text-primary-color)}button.secondary{background:transparent;color:var(--primary-color);border:1px solid var(--primary-color)}button.danger{background:var(--error-color,#db4437);color:#fff}.secondary-danger{margin-left:auto}button.icon{min-width:42px;padding:4px;background:transparent;color:var(--secondary-text-color);font-size:25px}button:disabled{opacity:.6;cursor:wait}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,[tabindex]:focus-visible,summary:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}nav{display:flex;overflow:auto;border-bottom:1px solid var(--divider-color);margin-bottom:28px}nav button{background:transparent;color:var(--secondary-text-color);border-radius:0;padding:13px 18px;white-space:nowrap}nav button.active{color:var(--primary-color);border-bottom:3px solid var(--primary-color)}main{display:grid;gap:30px}.scope-bar,.content-card,.metric,.notice{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:13px}.scope-bar{max-width:1220px;margin:0 auto 28px;padding:18px 22px;display:flex;align-items:end;gap:20px;flex-wrap:wrap}.scope-bar>label:first-child{min-width:min(420px,100%)}.scope-bar .show-empty{display:flex;grid-gap:8px;align-items:center;min-height:42px}.show-empty input{width:18px;min-height:18px}.scope-bar small{margin-left:auto}.content-card{padding:24px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:18px}.metric-grid.compact{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}.metric{padding:20px;display:grid;gap:7px}.metric span,.meta,small,.help,.counter{color:var(--secondary-text-color)}.metric strong{font-size:22px;font-weight:500}.section-heading{display:flex;align-items:start;justify-content:space-between;gap:24px;margin-bottom:20px}.section-heading h2,.content-card>h2{margin:0;font-size:20px}.search,.search-row{margin-bottom:20px}.search-row{display:flex;gap:12px}.search-row input{flex:1}.list{display:grid;gap:12px}.list-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:center;border:1px solid var(--divider-color);border-radius:11px;padding:17px}.card-main.clickable{cursor:pointer;border-radius:8px;padding:4px;margin:-4px}.card-main.clickable:hover{background:var(--secondary-background-color)}.list-card h3,.primary-copy{margin:0;font-size:16px;line-height:1.45;overflow-wrap:anywhere}.list-card .description{margin:5px 0;line-height:1.45;overflow-wrap:anywhere}.meta{margin:6px 0 0;font-size:12px;line-height:1.45}.actions,.section-actions,.dialog-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.actions button{min-height:38px;padding:7px 12px}.notice{padding:20px 22px;border-left:4px solid var(--warning-color,#f9ab00)}.notice.on{border-left-color:var(--success-color,#0f9d58)}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 22px}fieldset{border:0;border-top:1px solid var(--divider-color);padding:26px 0 4px;margin:24px 0 0}legend{padding-right:14px;font-size:16px;font-weight:600}.toggle{display:flex;align-items:center;justify-content:space-between;min-height:42px}.toggle input{width:42px;height:24px;min-height:24px;accent-color:var(--primary-color)}details{border-top:1px solid var(--divider-color);margin-top:28px;padding-top:20px}summary{cursor:pointer;font-weight:600;padding:8px 0}.advanced-body{display:grid;gap:12px;padding-top:12px}.json-editor{min-height:230px;font-family:var(--code-font-family,ui-monospace,monospace)}.validation{font-size:12px}.validation.valid{color:var(--success-color,#0f9d58)}.validation.invalid,.inline-error{color:var(--error-color,#db4437)}.section-actions{margin-top:24px}.chart{height:170px;display:flex;align-items:end;gap:5px;border-bottom:1px solid var(--divider-color);padding-top:12px}.chart span{flex:1;min-width:4px;max-width:28px;background:var(--primary-color);border-radius:4px 4px 0 0}.table{overflow:auto}table{border-collapse:collapse;width:100%;margin-top:12px}th,td{text-align:left;border-bottom:1px solid var(--divider-color);padding:11px;white-space:nowrap}.empty{text-align:center;color:var(--secondary-text-color);padding:34px 18px}.error{background:var(--error-color,#db4437);color:#fff;padding:15px;border-radius:9px}.loading{display:flex;align-items:center;justify-content:center;gap:10px;min-height:130px;color:var(--secondary-text-color)}.spinner{width:20px;height:20px;border:2px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}dialog{color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:14px;padding:0;width:min(620px,calc(100vw - 28px));max-height:calc(100vh - 28px);box-shadow:0 16px 50px rgba(0,0,0,.35)}dialog.wide{width:min(900px,calc(100vw - 28px))}dialog::backdrop{background:rgba(0,0,0,.5)}dialog form{margin:0}.dialog-header{padding:18px 22px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--divider-color)}.dialog-header h2{margin:0;font-size:20px}.dialog-body{padding:22px;display:grid;gap:18px;overflow:auto;max-height:calc(100vh - 155px)}.dialog-actions{justify-content:flex-end;padding:14px 22px 18px;border-top:1px solid var(--divider-color)}.dialog-actions>.danger:first-child{margin-right:auto}.short-textarea{min-height:80px}.knowledge-editor{height:52vh;min-height:320px;max-height:65vh;font-family:var(--code-font-family,ui-monospace,monospace)}#memory-content{min-height:150px}.counter{text-align:right;font-size:12px;margin-top:-12px}.inline-error:empty{display:none}.session-body{gap:16px}.turn{display:grid;gap:9px;border-bottom:1px solid var(--divider-color);padding-bottom:18px}.message{padding:13px 15px;border-radius:10px;background:var(--secondary-background-color)}.message.assistant{border-left:3px solid var(--primary-color)}.message.user{border-left:3px solid var(--accent-color,var(--warning-color,#f9ab00))}.message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0;line-height:1.55}.toast{position:fixed;right:24px;bottom:24px;z-index:10000;max-width:min(460px,calc(100vw - 32px));padding:13px 17px;border-radius:9px;background:var(--success-color,#0f9d58);color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transform:translateY(12px);pointer-events:none;transition:.2s}.toast.visible{opacity:1;transform:none}.toast.toast-error{background:var(--error-color,#db4437)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--secondary-background-color);padding:14px;border-radius:9px}code{font-family:var(--code-font-family,ui-monospace,monospace)}
    .config-toolbar{display:flex;gap:16px;justify-content:space-between;align-items:center}.config-toolbar>input{max-width:620px}.agent-actions{display:flex;gap:10px;flex-wrap:wrap}.action-help{margin:-18px 0 0;text-align:right;color:var(--secondary-text-color);font-size:12px}.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.config-card{align-self:start}.config-span{grid-column:1/-1}.config-stack{display:grid;gap:16px}.config-toggle{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:48px}.config-toggle>span{display:grid;gap:4px}.config-toggle input{width:42px;height:24px;min-height:24px;accent-color:var(--primary-color)}.dependent{display:grid;gap:16px;padding:4px 0 4px 18px;border-left:3px solid var(--divider-color)}.hidden{display:none!important}.prompt-editor{min-height:46vh;max-height:70vh;font-family:var(--code-font-family,ui-monospace,monospace);tab-size:2}.yaml-editor{min-height:240px;font-family:var(--code-font-family,ui-monospace,monospace);tab-size:2}.yaml-editor.tall{min-height:48vh}.editor-meta{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:10px;color:var(--secondary-text-color)}.compact-button{min-height:36px;padding:6px 11px}.subsection{border-top:1px solid var(--divider-color);margin-top:24px;padding-top:20px;display:grid;gap:14px}.rule-list{display:grid;gap:12px;margin-bottom:12px}.rule-row{border:1px solid var(--divider-color);border-radius:10px;padding:14px;display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:end}.rule-actions,.mode-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.rule-actions button{min-width:40px;padding:7px}.preview-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.save-bar{position:sticky;bottom:12px;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:18px;margin-top:4px;padding:14px 18px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.2)}.dirty-state{color:var(--secondary-text-color)}.field-error{min-height:0;color:var(--error-color,#db4437);font-size:12px}.field-error:empty{display:none}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
    @media(max-width:850px){:host{padding:20px}header{align-items:stretch;flex-direction:column;gap:20px}.agent-picker{width:100%}.form-grid,.config-grid,.preview-grid{grid-template-columns:1fr}.config-span{grid-column:auto}.config-toolbar{align-items:stretch;flex-direction:column}.config-toolbar>input{max-width:none}.scope-bar small{margin-left:0}.list-card{grid-template-columns:1fr}.actions{justify-content:flex-start}.rule-row{grid-template-columns:1fr}.save-bar{bottom:8px}}
    @media(max-width:600px){:host{padding:12px}.page-heading h1{font-size:26px}nav{margin-inline:-12px;padding-inline:4px}.content-card{padding:18px}.section-heading{flex-direction:column;align-items:stretch}.section-heading button{width:100%}.search-row{flex-direction:column}.scope-bar{padding:16px}.knowledge-editor{height:50vh;min-height:260px}.dialog-body{padding:18px}.dialog-header,.dialog-actions{padding-inline:18px}.actions button{flex:1}.secondary-danger{margin-left:0}}
  `; }
}

customElements.define("extended-openai-management-panel", ExtendedOpenAIManagementPanel);
