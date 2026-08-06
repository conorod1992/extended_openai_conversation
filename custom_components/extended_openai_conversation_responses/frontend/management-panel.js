const WS_TYPE = "extended_openai_conversation_responses/management";
const SECTIONS = ["overview", "usage", "conversations", "memories", "knowledge", "diagnostics"];

class ExtendedOpenAIManagementPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._section = this._sectionFromPath();
    this._data = null;
    this._result = null;
    this._busy = false;
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._loadAgents();
  }

  set route(value) {
    this._route = value;
    this._section = this._sectionFromPath();
    this._render();
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

  async _loadAgents() {
    try {
      this._data = await this._hass.callWS({ type: WS_TYPE, action: "agents" });
      const saved = localStorage.getItem("extended-openai-agent");
      const available = this._data.agents || [];
      this._agentId = available.some((item) => item.subentry_id === saved)
        ? saved
        : available[0]?.subentry_id;
      this._scopeId = this._data.scopes?.[0]?.scope_id;
      await this._loadSection();
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  _selectedAgent() {
    return this._data?.agents?.find((item) => item.subentry_id === this._agentId);
  }

  async _loadSection() {
    if (!this._selectedAgent()) return this._render();
    this._busy = true;
    this._render();
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
        const [summary, days, runs, breakdowns, retention] = await Promise.all([
          this._call("usage", "summary"), this._call("usage", "daily"),
          this._call("usage", "runs", { limit: 30 }), this._call("usage", "breakdowns"),
          this._call("usage", "retention"),
        ]);
        this._result = { summary, days, runs, breakdowns, retention };
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

  _navigate(section) {
    this._section = section;
    history.pushState({}, "", `/extended-openai/${section}`);
    this._result = null;
    this._loadSection();
  }

  _render() {
    const agent = this._selectedAgent();
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card>
        <header><div><h1>Extended OpenAI</h1><p>Local management for conversation agents</p></div>
          <label>Agent<select id="agent">${(this._data?.agents || []).map((a) => `<option value="${this._e(a.subentry_id)}" ${a.subentry_id === this._agentId ? "selected" : ""}>${this._e(a.title)}</option>`).join("")}</select></label>
        </header>
        <nav>${SECTIONS.map((s) => `<button data-section="${s}" class="${s === this._section ? "active" : ""}">${this._label(s)}</button>`).join("")}</nav>
        ${["conversations", "memories"].includes(this._section) ? this._scopePicker() : ""}
        <main>${!agent ? this._empty("No conversation agents configured.") : this._busy ? this._empty("Loading…") : this._error ? `<div class="error">${this._e(this._error)}</div>` : this._content(agent)}</main>
      </ha-card>`;
    this.shadowRoot.querySelectorAll("nav button").forEach((button) => button.onclick = () => this._navigate(button.dataset.section));
    const agentSelect = this.shadowRoot.querySelector("#agent");
    if (agentSelect) agentSelect.onchange = () => { this._agentId = agentSelect.value; localStorage.setItem("extended-openai-agent", this._agentId); this._loadSection(); };
    const scopeSelect = this.shadowRoot.querySelector("#scope");
    if (scopeSelect) scopeSelect.onchange = () => { this._scopeId = scopeSelect.value; this._loadSection(); };
    this._bindActions();
  }

  _content(agent) {
    if (this._section === "overview") return this._overview(agent);
    if (this._section === "usage") return this._usage();
    if (this._section === "conversations") return this._conversations();
    if (this._section === "memories") return this._memories();
    if (this._section === "knowledge") return this._knowledge();
    return this._diagnostics(agent);
  }

  _overview(agent) {
    const u = this._result?.usage || {};
    return `<section class="grid">
      ${this._card("Provider & model", `${agent.provider}<br>${agent.model}`)}
      ${this._card("Tokens today", u.today?.total_tokens ?? 0)}
      ${this._card("Tokens this month", u.month?.total_tokens ?? 0)}
      ${this._card("Lifetime tokens", u.lifetime?.total_tokens ?? 0)}
      ${this._card("Latest response", u.latest?.total_tokens ?? "—")}
      ${this._card("Memories", `${agent.memory_mode} · ${agent.memory_count}`)}
      ${this._card("Knowledge", `${agent.knowledge_enabled ? "Enabled" : "Disabled"} · ${agent.knowledge_source_count}`)}
      ${this._card("Archive", agent.archive_enabled ? "Enabled" : "Disabled")}
    </section>`;
  }

  _usage() {
    const r = this._result || {};
    const days = r.days?.days || [];
    const max = Math.max(1, ...days.map((d) => d.total_tokens));
    return `<section class="grid summary">${this._card("Today", r.summary?.today?.total_tokens || 0)}${this._card("This month", r.summary?.month?.total_tokens || 0)}${this._card("Lifetime", r.summary?.lifetime?.total_tokens || 0)}${this._card("Latest response", r.summary?.latest?.total_tokens ?? "—")}</section>
      <section><h2>Tokens by day</h2><div class="chart">${days.slice(-31).map((d) => `<span title="${this._e(d.date)}: ${d.total_tokens}" style="height:${Math.max(2, d.total_tokens / max * 100)}%"></span>`).join("") || this._empty("No completed runs yet.")}</div></section>
      <section><h2>Recent runs</h2>${this._table(["Completed", "Tokens", "Requests", "Duration", "Result"], (r.runs?.runs || []).map((run) => [run.completed_at || "—", run.total_tokens, run.request_count, `${run.duration_ms} ms`, run.successful ? "Success" : run.error_type || "Failed"]))}</section>
      ${this._data?.is_admin ? `<section><h2>Detail retention</h2><div class="row"><label>Requests<select id="request-retention">${this._retentionOptions(r.retention?.request_days)}</select></label><label>Runs<select id="run-retention">${this._retentionOptions(r.retention?.run_days)}</select></label><button id="save-usage-settings">Save</button><button id="clear-details" class="danger">Clear recent details</button></div><small>Daily, monthly, and lifetime totals are never removed by detail pruning.</small></section>` : ""}`;
  }

  _conversations() {
    const r = this._result || {};
    const s = r.settings || {};
    return `<section class="notice ${s.archive_enabled ? "on" : ""}"><strong>Archive ${s.archive_enabled ? "enabled" : "disabled"}</strong><span>${s.archive_retention_days || 30}-day retention · Model search ${s.archive_model_search_enabled ? "on" : "off"}</span></section>
      <section><div class="row"><input id="archive-query" placeholder="Search retained discussions"><button id="archive-search">Search</button></div>
      <h2>Retained sessions</h2><div class="list">${(r.sessions?.sessions || []).map((item) => `<article><button class="link open-session" data-id="${item.session_id}">${this._e(item.title || "Untitled conversation")}</button><small>${this._e(item.last_message_at)} · ${item.turn_count} turns · ${this._e(item.scope_source)}</small><button class="danger delete-session" data-id="${item.session_id}">Delete</button></article>`).join("") || this._empty("No retained conversations in this scope.")}</div></section>
      <section id="session-detail"></section>
      ${this._data?.is_admin ? `<section><h2>Archive and voice settings</h2><div class="settings-grid">
        <label><span>Archive enabled</span><input id="archive-enabled" type="checkbox" ${s.archive_enabled ? "checked" : ""}></label>
        <label>Retention<select id="archive-retention">${[7,30,90,180,365].map((v) => `<option value="${v}" ${v === s.archive_retention_days ? "selected" : ""}>${v} days</option>`).join("")}</select></label>
        <label><span>Model search</span><input id="archive-model-search" type="checkbox" ${s.archive_model_search_enabled ? "checked" : ""}></label>
        <label><span>Shared archive</span><input id="shared-archive" type="checkbox" ${s.shared_archive_enabled ? "checked" : ""}></label>
        <label>Unidentified voice<select id="voice-policy">${["unretained","shared","default_user","device_mapping"].map((v) => `<option value="${v}" ${v === s.voice_scope_policy ? "selected" : ""}>${v.replaceAll("_", " ")}</option>`).join("")}</select></label>
        <label>Unmapped fallback<select id="voice-fallback">${["unretained","shared","default_user"].map((v) => `<option value="${v}" ${v === s.voice_unmapped_policy ? "selected" : ""}>${v.replaceAll("_", " ")}</option>`).join("")}</select></label>
        <label>Default owner ID<input id="voice-owner" value="${this._e(s.voice_default_user_id || "")}"></label>
        <label>Shared memory<select id="shared-memory">${["disabled","explicit","automatic"].map((v) => `<option value="${v}" ${v === s.shared_memory_mode ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      </div><label>Satellite mappings (JSON object)<textarea id="device-mappings">${this._e(JSON.stringify(s.voice_device_mappings || {}, null, 2))}</textarea></label><p><button id="save-conversation-settings">Save settings</button></p></section>` : ""}`;
  }

  _memories() {
    const items = this._result?.memories || [];
    return `<section><div class="row"><input id="memory-content" placeholder="Add a concise memory"><input id="memory-category" value="general" aria-label="Category"><button id="add-memory">Add</button></div>
      <div class="list">${items.map((m) => `<article><strong>${this._e(m.content)}</strong><small>${this._e(m.category)} · ${this._e(m.source)} · ${this._e(m.updated_at)}</small><div class="actions"><button class="edit-memory" data-id="${m.memory_id}">Edit</button>${this._data?.is_admin && this._scopeId === "__anonymous__" ? `<button class="reassign-memory" data-id="${m.memory_id}">Reassign</button>` : ""}<button class="danger delete-memory" data-id="${m.memory_id}">Delete</button></div></article>`).join("") || this._empty("No memories in this scope.")}</div>
      ${this._data?.is_admin && this._scopeId === "__anonymous__" ? `<p>Legacy anonymous memories remain unassigned until an administrator explicitly reassigns or deletes them.</p>` : ""}</section>`;
  }

  _knowledge() {
    const items = this._result?.sources || [];
    return `<section><div class="row"><input id="source-title" placeholder="Source title"><input id="source-description" placeholder="Description"><button id="create-source">Create</button></div><label>Source content<textarea id="source-content"></textarea></label><div class="list">${items.map((s) => `<article><strong>${this._e(s.title)}</strong><small>${this._e(s.description || "")} · ${s.character_count || 0} characters</small><div class="actions"><button class="edit-source" data-id="${s.source_id}">Edit</button><button class="danger delete-source" data-id="${s.source_id}">Delete</button></div></article>`).join("") || this._empty("No Knowledge Library sources.")}</div></section>`;
  }

  _diagnostics(agent) {
    return `<section class="grid">${this._card("Agent", agent.title)}${this._card("Provider", agent.provider)}${this._card("Model", agent.model)}${this._card("Archive", agent.archive_enabled ? "Enabled" : "Disabled")}</section><section><h2>Safe agent test</h2><p>Tests one minimal provider response without executing Home Assistant actions.</p><button id="test-agent">Test Agent</button><pre id="test-result"></pre></section>`;
  }

  _bindActions() {
    const q = (s) => this.shadowRoot.querySelector(s);
    if (q("#clear-details")) q("#clear-details").onclick = async () => { if (confirm("Clear recent request and run details? Lifetime and daily totals will remain.")) { await this._call("usage", "clear_details", { confirm: true }); this._loadSection(); } };
    if (q("#save-usage-settings")) q("#save-usage-settings").onclick = async () => { await this._call("settings", "update", { settings: { usage_request_retention_days: Number(q("#request-retention").value), usage_run_retention_days: Number(q("#run-retention").value) } }); this._loadSection(); };
    if (q("#save-conversation-settings")) q("#save-conversation-settings").onclick = async () => { try { const mappings = JSON.parse(q("#device-mappings").value || "{}"); await this._call("settings", "update", { settings: { archive_enabled: q("#archive-enabled").checked, archive_retention_days: Number(q("#archive-retention").value), archive_model_search_enabled: q("#archive-model-search").checked, shared_archive_enabled: q("#shared-archive").checked, voice_scope_policy: q("#voice-policy").value, voice_unmapped_policy: q("#voice-fallback").value, voice_default_user_id: q("#voice-owner").value, voice_device_mappings: mappings, shared_memory_mode: q("#shared-memory").value } }); this._loadSection(); } catch (err) { alert(err.message || String(err)); } };
    if (q("#archive-search")) q("#archive-search").onclick = async () => { const found = await this._call("conversations", "search", { scope_id: this._scopeId, query: q("#archive-query").value, limit: 20 }); this._result.sessions = { sessions: found.results.map((x) => ({...x, last_message_at: x.timestamp, turn_count: "matching"})) }; this._render(); };
    this.shadowRoot.querySelectorAll(".open-session").forEach((b) => b.onclick = async () => { const data = await this._call("conversations", "get", { scope_id: this._scopeId, session_id: b.dataset.id, limit: 50 }); q("#session-detail").innerHTML = `<h2>${this._e(data.session.title)}</h2>${data.turns.map((t) => `<div class="turn"><strong>You</strong><p>${this._e(t.user_text)}</p><strong>Assistant</strong><p>${this._e(t.assistant_text)}</p><small>${this._e(t.timestamp)}</small></div>`).join("")}`; });
    this.shadowRoot.querySelectorAll(".delete-session").forEach((b) => b.onclick = async () => { if (confirm(`Delete this conversation from ${this._scopeId}?`)) { await this._call("conversations", "delete", { scope_id: this._scopeId, session_id: b.dataset.id }); this._loadSection(); } });
    if (q("#add-memory")) q("#add-memory").onclick = async () => { await this._call("memories", "add", { scope_id: this._scopeId, content: q("#memory-content").value, category: q("#memory-category").value }); this._loadSection(); };
    this.shadowRoot.querySelectorAll(".edit-memory").forEach((b) => b.onclick = async () => { const current = (this._result?.memories || []).find((m) => m.memory_id === b.dataset.id); if (!current) return; const content = prompt("Memory", current.content); if (content == null) return; const category = prompt("Category", current.category); if (category == null) return; await this._call("memories", "update", { scope_id: this._scopeId, memory_id: b.dataset.id, content, category }); this._loadSection(); });
    this.shadowRoot.querySelectorAll(".reassign-memory").forEach((b) => b.onclick = async () => { const target = prompt("Target scope ID (user:<Home Assistant user ID> or shared:household)", "shared:household"); if (!target) return; const result = await this._call("memories", "reassign_legacy", { scope_id: "__anonymous__", target_scope_id: target, memory_ids: [b.dataset.id] }); alert(`Reassigned ${result.reassigned} memory record(s).`); this._loadSection(); });
    this.shadowRoot.querySelectorAll(".delete-memory").forEach((b) => b.onclick = async () => { if (confirm(`Delete this memory from ${this._scopeId}?`)) { await this._call("memories", "delete", { scope_id: this._scopeId, memory_id: b.dataset.id }); this._loadSection(); } });
    if (q("#create-source")) q("#create-source").onclick = async () => { await this._call("knowledge", "create", { title: q("#source-title").value, description: q("#source-description").value, content: q("#source-content").value }); this._loadSection(); };
    this.shadowRoot.querySelectorAll(".edit-source").forEach((b) => b.onclick = async () => { const data = await this._call("knowledge", "get", { source_id: b.dataset.id }); const source = data.source; const title = prompt("Title", source.title); if (title == null) return; const description = prompt("Description", source.description || ""); if (description == null) return; const content = prompt("Content", source.content); if (content == null) return; await this._call("knowledge", "update", { source_id: b.dataset.id, title, description, content }); this._loadSection(); });
    this.shadowRoot.querySelectorAll(".delete-source").forEach((b) => b.onclick = async () => { if (confirm("Delete this Knowledge Library source?")) { await this._call("knowledge", "delete", { source_id: b.dataset.id, confirm: true }); this._loadSection(); } });
    if (q("#test-agent")) q("#test-agent").onclick = async () => { q("#test-result").textContent = "Testing…"; try { q("#test-result").textContent = JSON.stringify(await this._call("diagnostics", "test_agent"), null, 2); } catch (err) { q("#test-result").textContent = err.message || String(err); } };
  }

  _scopePicker() { return `<div class="scope"><span>Archive/memory scope:</span><select id="scope">${(this._data?.scopes || []).map((s) => `<option value="${this._e(s.scope_id)}" ${s.scope_id === this._scopeId ? "selected" : ""}>${this._e(s.display_name)} · ${this._e(s.scope_type)}</option>`).join("")}</select>${this._data?.is_admin ? "<small>Viewing as Home Assistant administrator</small>" : ""}</div>`; }
  _retentionOptions(selected) { return [0,7,30,90,180,365].map((v) => `<option value="${v}" ${v === selected ? "selected" : ""}>${v ? `${v} days` : "Disabled"}</option>`).join(""); }
  _card(title, value) { return `<article class="card"><span>${this._e(title)}</span><strong>${value}</strong></article>`; }
  _table(headers, rows) { return `<div class="table"><table><thead><tr>${headers.map((h) => `<th>${this._e(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${this._e(String(v))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }
  _empty(text) { return `<p class="empty">${this._e(text)}</p>`; }
  _label(value) { return value[0].toUpperCase() + value.slice(1); }
  _e(value) { const div = document.createElement("div"); div.textContent = value == null ? "" : String(value); return div.innerHTML; }
  _styles() { return `:host{display:block;padding:16px;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui)}ha-card{max-width:1200px;margin:auto;overflow:hidden}header{display:flex;justify-content:space-between;gap:24px;align-items:end;padding:24px}h1{margin:0;font-size:28px}h2{font-size:18px;margin:0 0 16px}p{line-height:1.5}header p{margin:4px 0 0;color:var(--secondary-text-color)}label,header label{display:grid;gap:6px;font-size:12px;color:var(--secondary-text-color)}select,input,textarea,button{font:inherit;border:1px solid var(--divider-color);border-radius:8px;padding:10px;background:var(--card-background-color);color:var(--primary-text-color)}textarea{min-height:120px;font-family:monospace}button{cursor:pointer;background:var(--primary-color);color:var(--text-primary-color);border:0}nav{display:flex;overflow:auto;border-block:1px solid var(--divider-color);padding:0 16px}nav button{background:transparent;color:var(--secondary-text-color);border-radius:0;padding:14px 16px;white-space:nowrap}nav button.active{color:var(--primary-color);border-bottom:3px solid var(--primary-color)}main{padding:24px}section{margin-bottom:28px}.grid,.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}.settings-grid{margin-bottom:16px}.settings-grid label:has(input[type=checkbox]){display:flex;align-items:center;justify-content:space-between}.card{border:1px solid var(--divider-color);border-radius:12px;padding:18px;display:grid;gap:8px}.card span,.scope span,small{color:var(--secondary-text-color)}.card strong{font-size:21px}.scope{display:flex;align-items:center;gap:12px;padding:12px 24px;background:var(--secondary-background-color);flex-wrap:wrap}.scope small{margin-left:auto}.row{display:flex;gap:10px;margin-bottom:18px}.row input:first-child{flex:1}.list{display:grid;gap:10px}.list article{display:grid;grid-template-columns:1fr auto;gap:5px 16px;align-items:center;border:1px solid var(--divider-color);border-radius:10px;padding:14px}.list strong,.list .link{grid-column:1}.list small{grid-column:1}.list .actions{grid-column:2;grid-row:1/3;display:flex;gap:6px}.list button{padding:8px}.link{background:transparent;color:var(--primary-color);padding:0;text-align:left;font-weight:600}.danger{background:var(--error-color,#db4437);color:white}.notice{display:flex;justify-content:space-between;gap:16px;border-left:4px solid var(--warning-color,#f9ab00);padding:14px;background:var(--secondary-background-color)}.notice.on{border-color:var(--success-color,#0f9d58)}.chart{height:160px;display:flex;align-items:end;gap:4px;border-bottom:1px solid var(--divider-color);padding-top:10px}.chart span{flex:1;min-width:3px;max-width:24px;background:var(--primary-color);border-radius:3px 3px 0 0}.table{overflow:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid var(--divider-color);padding:10px;white-space:nowrap}.turn{border-left:3px solid var(--primary-color);padding:8px 16px;margin:12px 0}.turn p{white-space:pre-wrap}.error{background:var(--error-color);color:white;padding:14px;border-radius:8px}.empty{color:var(--secondary-text-color);text-align:center;padding:24px}pre{white-space:pre-wrap;background:var(--secondary-background-color);padding:12px;border-radius:8px}@media(max-width:700px){:host{padding:0}header{align-items:stretch;flex-direction:column}nav{padding:0}main{padding:16px}.scope{padding:12px 16px}.row{flex-direction:column}.notice{flex-direction:column}.list article{grid-template-columns:1fr}.list .actions{grid-column:1;grid-row:auto}.scope small{margin-left:0}}`; }
}

customElements.define("extended-openai-management-panel", ExtendedOpenAIManagementPanel);
