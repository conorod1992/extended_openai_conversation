export const GUEST_EXCLUSION_KEYS = [
  "guest_excluded_labels", "guest_excluded_areas", "guest_excluded_domains", "guest_excluded_entities",
  "guest_control_excluded_labels", "guest_control_excluded_areas", "guest_control_excluded_domains", "guest_control_excluded_entities",
];

const MEMORY_PAGE_SIZE = 100;
const ARCHIVE_PAGE_SIZE = 50;
const MEMORY_SEARCH_DEBOUNCE_MS = 250;
const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai-management-browser");

export function freshGuestPolicyDraft(config = {}) {
  const draft = JSON.parse(JSON.stringify(config));
  [...GUEST_EXCLUSION_KEYS, "guest_knowledge_source_ids", "guest_allowed_function_names", "guest_allowed_group_ids"]
    .forEach((key) => { draft[key] = []; });
  return Object.assign(draft, {
    guest_mode_enabled: true,
    guest_separate_control_restrictions: false,
    guest_knowledge_policy: "off",
    guest_function_policy: "off",
    guest_shared_memory_policy: "off",
  });
}

export function formatManagementTimestamp(value, timeZone) {
  if (!value) return "Unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try { return date.toLocaleString(undefined, timeZone ? {timeZone} : undefined); }
  catch (_) { return date.toLocaleString(); }
}

export function memorySearchProjection(memory) {
  return `${memory?.content ?? ""} ${memory?.category ?? ""} ${memory?.source ?? ""}`.toLocaleLowerCase();
}

function browserState(panel) {
  if (!panel._managementBrowserState) {
    panel._managementBrowserState = {
      archiveQuery: "",
      memoryQuery: "",
      memorySearchTimer: null,
      memorySearchSequence: 0,
      projections: new Map(),
      session: null,
    };
  }
  return panel._managementBrowserState;
}

function resultTarget(panel) {
  return panel._contentData || panel._result;
}

function indexMemories(panel) {
  const state = browserState(panel);
  state.projections = new Map(
    (panel._result?.memories || []).map((memory) => [memory.memory_id, memorySearchProjection(memory)]),
  );
}

function memoryCard(panel, memory) {
  return `<article class="list-card" data-memory-id="${panel._e(memory.memory_id)}"><div class="card-main clickable edit-memory" tabindex="0" role="button" data-id="${panel._e(memory.memory_id)}"><p class="primary-copy">${panel._e(memory.content)}</p><p class="meta">${panel._e(memory.category)} · ${panel._e(memory.source)} · Updated ${panel._e(panel._formatDate(memory.updated_at))}</p></div><div class="actions"><button type="button" class="secondary memory-edit-button" data-id="${panel._e(memory.memory_id)}">Edit</button>${panel._data?.is_admin && panel._scopeId === "__anonymous__" ? `<button type="button" class="secondary reassign-memory" data-id="${panel._e(memory.memory_id)}">Assign to user</button>` : ""}<button type="button" class="danger delete-memory" data-id="${panel._e(memory.memory_id)}">Delete</button></div></article>`;
}

async function runMemorySearch(panel) {
  const state = browserState(panel);
  const query = panel._query.trim();
  const sequence = ++state.memorySearchSequence;
  const action = query ? "search" : "list";
  try {
    const result = await panel._call("memories", action, {
      scope_id: panel._scopeId,
      query,
      limit: MEMORY_PAGE_SIZE,
      offset: 0,
    });
    if (sequence !== state.memorySearchSequence || panel._viewKey() !== "data-memory/memories" || panel._memoryKind !== "persistent" || panel._query.trim() !== query) return;
    panel._result = result;
    state.memoryQuery = query.toLocaleLowerCase();
    indexMemories(panel);
    panel._render();
  } catch (err) {
    if (sequence === state.memorySearchSequence) panel._toast(`Unable to search memories: ${err.message || String(err)}`, true);
  }
}

function scheduleMemorySearch(panel) {
  const state = browserState(panel);
  clearTimeout(state.memorySearchTimer);
  state.memorySearchTimer = setTimeout(() => runMemorySearch(panel), MEMORY_SEARCH_DEBOUNCE_MS);
}

async function loadMoreMemories(panel, button) {
  const state = browserState(panel);
  const current = panel._result?.memories || [];
  const query = state.memoryQuery;
  panel._setSaving(button, true, "Loading…");
  try {
    const result = await panel._call("memories", query ? "search" : "list", {
      scope_id: panel._scopeId,
      query,
      limit: MEMORY_PAGE_SIZE,
      offset: current.length,
    });
    panel._result = {...result, memories: [...current, ...(result.memories || [])]};
    indexMemories(panel);
    panel._render();
  } catch (err) {
    panel._toast(`Unable to load more memories: ${err.message || String(err)}`, true);
  } finally {
    panel._setSaving(button, false);
  }
}

function mappedArchiveResults(found) {
  return {
    sessions: (found.results || []).map((item) => ({
      ...item,
      last_message_at: item.timestamp,
      turn_count: "matching",
      scope_source: "Search match",
    })),
    offset: found.offset || 0,
    limit: found.limit || ARCHIVE_PAGE_SIZE,
    has_more: Boolean(found.has_more),
  };
}

async function loadMoreConversations(panel, button) {
  const state = browserState(panel);
  const target = resultTarget(panel);
  const current = target?.sessions?.sessions || [];
  panel._setSaving(button, true, "Loading…");
  try {
    const result = state.archiveQuery
      ? await panel._call("conversations", "search", {scope_id: panel._scopeId, query: state.archiveQuery, limit: ARCHIVE_PAGE_SIZE, offset: current.length})
      : await panel._call("conversations", "list", {scope_id: panel._scopeId, limit: ARCHIVE_PAGE_SIZE, offset: current.length});
    const next = state.archiveQuery ? mappedArchiveResults(result) : result;
    target.sessions = {...next, sessions: [...current, ...(next.sessions || [])]};
    panel._render();
  } catch (err) {
    panel._toast(`Unable to load more conversations: ${err.message || String(err)}`, true);
  } finally {
    panel._setSaving(button, false);
  }
}

function renderSessionBody(panel) {
  const state = browserState(panel).session;
  const body = panel.shadowRoot.querySelector("#session-body");
  if (!body || !state) return;
  const turns = state.turns.map((turn) => `<article class="turn"><div class="message user"><strong>You</strong><p>${panel._e(turn.user_text)}</p></div><div class="message assistant"><strong>Assistant</strong><p>${panel._e(turn.assistant_text)}</p></div><small>${panel._e(panel._formatDate(turn.timestamp))}</small></article>`).join("") || panel._empty("No turns retained.");
  body.innerHTML = `${turns}${state.hasMore ? `<div class="section-actions"><button type="button" class="secondary" id="load-more-turns">Load more turns</button></div>` : ""}`;
  body.querySelector("#load-more-turns")?.addEventListener("click", (event) => loadMoreTurns(panel, event.currentTarget));
}

async function loadMoreTurns(panel, button) {
  const state = browserState(panel).session;
  if (!state) return;
  panel._setSaving(button, true, "Loading…");
  try {
    const data = await panel._call("conversations", "get", {
      scope_id: panel._scopeId,
      session_id: state.sessionId,
      start_turn: state.turns.length,
      limit: ARCHIVE_PAGE_SIZE,
    });
    state.turns.push(...(data.turns || []));
    state.hasMore = Boolean(data.has_more);
    renderSessionBody(panel);
  } catch (err) {
    panel._toast(`Unable to load more turns: ${err.message || String(err)}`, true);
  } finally {
    panel._setSaving(button, false);
  }
}

export function installManagementBrowser(Panel) {
  if (!Panel?.prototype || Panel.prototype[PATCHED]) return false;
  const proto = Panel.prototype;
  const originalLoadSection = proto._loadSection;
  const originalMemories = proto._memories;
  const originalConversations = proto._conversations;
  const originalBindActions = proto._bindActions;
  const originalUpdateVisibleList = proto._updateVisibleList;

  proto._formatDate = function(value) {
    return formatManagementTimestamp(value, this._hass?.config?.time_zone);
  };

  proto._loadSection = async function(...args) {
    const value = await originalLoadSection.apply(this, args);
    const state = browserState(this);
    if (this._viewKey() === "data-memory/memories" && this._memoryKind === "persistent") {
      state.memoryQuery = "";
      indexMemories(this);
    }
    if (this._viewKey() === "data-memory/conversations") state.archiveQuery = "";
    return value;
  };

  proto._memories = function() {
    if (this._memoryKind === "temporary") return originalMemories.call(this);
    const state = browserState(this);
    const query = this._query.trim().toLocaleLowerCase();
    const all = this._result?.memories || [];
    const items = query && query !== state.memoryQuery
      ? all.filter((memory) => (state.projections.get(memory.memory_id) || "").includes(query))
      : all;
    return `<section class="content-card"><div class="section-heading"><div><h2>Memories</h2><p>Long-term facts the assistant can reuse in future conversations.</p></div><button type="button" id="add-memory">+ Add memory</button></div><div class="config-jumps"><button type="button" class="secondary memory-kind" data-kind="persistent" disabled>Long-term</button><button type="button" class="secondary memory-kind" data-kind="temporary">Short-term</button></div><input id="list-search" class="search" type="search" value="${this._e(this._query)}" placeholder="Search memories" aria-label="Search memories"><div class="list memory-list">${items.map((memory) => memoryCard(this, memory)).join("") || this._empty(query ? "No memories match this search." : "No long-term memories yet.")}</div>${this._result?.has_more ? `<div class="section-actions"><button type="button" class="secondary" id="load-more-memories">Load more ${state.memoryQuery ? "matches" : "memories"}</button></div>` : ""}</section>`;
  };

  proto._conversations = function() {
    let html = originalConversations.call(this);
    const state = browserState(this);
    const sessions = resultTarget(this)?.sessions;
    if (state.archiveQuery) html = html.replace('id="archive-query" type="search"', `id="archive-query" type="search" value="${this._e(state.archiveQuery)}"`);
    if (!sessions?.has_more) return html;
    const marker = html.lastIndexOf("</section>");
    if (marker < 0) return html;
    const pagination = `<div class="section-actions"><button type="button" class="secondary" id="load-more-conversations">Load more ${state.archiveQuery ? "matches" : "conversations"}</button></div>`;
    return `${html.slice(0, marker)}${pagination}${html.slice(marker)}`;
  };

  proto._updateVisibleList = function() {
    if (this._viewKey() !== "data-memory/memories" || this._memoryKind !== "persistent") return originalUpdateVisibleList.call(this);
    const state = browserState(this);
    const query = this._query.trim().toLocaleLowerCase();
    this.shadowRoot.querySelectorAll(".memory-list .list-card").forEach((card) => {
      card.hidden = Boolean(query && !(state.projections.get(card.dataset.memoryId) || "").includes(query));
    });
    scheduleMemorySearch(this);
  };

  proto._searchArchive = async function() {
    const input = this.shadowRoot.querySelector("#archive-query");
    const button = this.shadowRoot.querySelector("#archive-search");
    if (!input || !button || button.disabled) return;
    const query = input.value.trim();
    const state = browserState(this);
    this._setSaving(button, true, "Searching…");
    try {
      const target = resultTarget(this);
      if (query) {
        const found = await this._call("conversations", "search", {scope_id: this._scopeId, query, limit: ARCHIVE_PAGE_SIZE, offset: 0});
        target.sessions = mappedArchiveResults(found);
      } else {
        target.sessions = await this._call("conversations", "list", {scope_id: this._scopeId, limit: ARCHIVE_PAGE_SIZE, offset: 0});
      }
      state.archiveQuery = query;
      this._render();
    } catch (err) {
      this._toast(`Unable to search: ${err.message || String(err)}`, true);
    } finally {
      this._setSaving(button, false);
    }
  };

  proto._openSession = async function(sessionId) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#session-dialog");
    root.querySelector("#session-body").innerHTML = this._loading();
    dialog.showModal();
    try {
      const data = await this._call("conversations", "get", {scope_id: this._scopeId, session_id: sessionId, start_turn: 0, limit: ARCHIVE_PAGE_SIZE});
      root.querySelector("#session-title").textContent = data.session.title || "Untitled conversation";
      browserState(this).session = {sessionId, turns: [...(data.turns || [])], hasMore: Boolean(data.has_more)};
      renderSessionBody(this);
    } catch (err) {
      root.querySelector("#session-body").innerHTML = `<div class="error" role="alert">${this._e(err.message || String(err))}</div>`;
    }
  };

  proto._bindActions = function(...args) {
    const value = originalBindActions.apply(this, args);
    this.shadowRoot.querySelector("#load-more-memories")?.addEventListener("click", (event) => loadMoreMemories(this, event.currentTarget));
    this.shadowRoot.querySelector("#load-more-conversations")?.addEventListener("click", (event) => loadMoreConversations(this, event.currentTarget));
    return value;
  };

  Object.defineProperty(proto, PATCHED, {value: true});
  return true;
}

const registry = globalThis.customElements;
if (registry?.whenDefined) {
  registry.whenDefined(PANEL_TAG).then(() => {
    const Panel = registry.get(PANEL_TAG);
    if (Panel) installManagementBrowser(Panel);
  });
}
