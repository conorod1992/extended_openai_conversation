export const GUEST_EXCLUSION_KEYS = [
  "guest_excluded_labels", "guest_excluded_areas", "guest_excluded_domains", "guest_excluded_entities",
  "guest_control_excluded_labels", "guest_control_excluded_areas", "guest_control_excluded_domains", "guest_control_excluded_entities",
];

const MEMORY_PAGE_SIZE = 100;
const ARCHIVE_PAGE_SIZE = 50;
const MEMORY_SEARCH_DEBOUNCE_MS = 250;

export function freshGuestPolicyDraft(config = {}) {
  const draft = JSON.parse(JSON.stringify(config));
  [...GUEST_EXCLUSION_KEYS, "guest_knowledge_source_ids", "guest_allowed_function_names", "guest_allowed_group_ids"]
    .forEach((key) => { draft[key] = []; });
  return Object.assign(draft, {
    guest_mode_enabled: true,
    guest_web_search: false,
    guest_separate_control_restrictions: false,
    guest_knowledge_policy: "off",
    guest_function_policy: "off",
    guest_shared_memory_policy: "off",
  });
}

export function renderGuestWebSearchSetting(config = {}) {
  return `<section class="content-card guest-hosted-capabilities"><div class="section-heading"><div><h2>Hosted capabilities</h2><p>Provider-hosted capabilities remain unavailable to guests unless you explicitly allow them.</p></div></div><label class="toggle"><span>Allow hosted Web Search</span><input id="guest-web-search" type="checkbox" ${config.guest_web_search ? "checked" : ""}></label><p class="help">Off by default. When enabled, Guest Mode may expose hosted Web Search only when this agent's provider, API mode, and Web Search configuration support it.</p></section>`;
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
  const query = state.memoryQuery ? panel._query.trim() : "";
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
  if (!target) return;
  const current = target.sessions?.sessions || [];
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

export function prepareMemoryBrowser(panel) {
  const state = browserState(panel);
  clearTimeout(state.memorySearchTimer);
  state.memorySearchSequence += 1;
  const view = panel._viewKey();
  if (view === "data-memory/memories" && panel._memoryKind === "persistent") state.memoryQuery = "";
  if (view === "data-memory/conversations") state.archiveQuery = "";
}

export async function finishMemoryBrowserLoad(panel) {
  if (panel._viewKey() === "data-memory/memories" && panel._memoryKind === "persistent") {
    indexMemories(panel);
    if (panel._query.trim()) await runMemorySearch(panel);
  }
}

export function renderPersistentMemories(panel) {
  const state = browserState(panel);
  const query = panel._query.trim().toLocaleLowerCase();
  const all = panel._result?.memories || [];
  const items = query && query !== state.memoryQuery
    ? all.filter((memory) => (state.projections.get(memory.memory_id) || "").includes(query))
    : all;
  return `<section class="content-card"><div class="section-heading"><div><h2>Memories</h2><p>Long-term facts the assistant can reuse in future conversations.</p></div><button type="button" id="add-memory">+ Add memory</button></div><div class="config-jumps"><button type="button" class="secondary memory-kind" data-kind="persistent" disabled>Long-term</button><button type="button" class="secondary memory-kind" data-kind="temporary">Short-term</button></div><input id="list-search" class="search" type="search" value="${panel._e(panel._query)}" placeholder="Search memories" aria-label="Search memories"><div class="list memory-list">${items.map((memory) => memoryCard(panel, memory)).join("") || panel._empty(query ? "No memories match this search." : "No long-term memories yet.")}</div>${panel._result?.has_more ? `<div class="section-actions"><button type="button" class="secondary" id="load-more-memories">Load more ${state.memoryQuery ? "matches" : "memories"}</button></div>` : ""}</section>`;
}

export function decorateConversations(panel, html) {
  const state = browserState(panel);
  const sessions = resultTarget(panel)?.sessions;
  if (state.archiveQuery) html = html.replace('id="archive-query" type="search"', `id="archive-query" type="search" value="${panel._e(state.archiveQuery)}"`);
  if (!sessions?.has_more) return html;
  const marker = html.lastIndexOf("</section>");
  if (marker < 0) return html;
  const pagination = `<div class="section-actions"><button type="button" class="secondary" id="load-more-conversations">Load more ${state.archiveQuery ? "matches" : "conversations"}</button></div>`;
  return `${html.slice(0, marker)}${pagination}${html.slice(marker)}`;
}

export function decorateGuestPolicy(panel, html) {
  const marker = '<section class="content-card"><div class="section-heading"><div><h2>Assistant permission</h2>';
  if (!html.includes(marker)) return html;
  const config = panel._guestDraft || panel._result?.config || {};
  return html.replace(marker, `${renderGuestWebSearchSetting(config)}${marker}`);
}

export function filterPersistentMemories(panel) {
  const state = browserState(panel);
  const query = panel._query.trim().toLocaleLowerCase();
  panel.shadowRoot.querySelectorAll(".memory-list .list-card").forEach((card) => {
    card.hidden = Boolean(query && !(state.projections.get(card.dataset.memoryId) || "").includes(query));
  });
  if (query !== state.memoryQuery) scheduleMemorySearch(panel);
}

export function bindMemoryBrowser(panel) {
  panel.shadowRoot.querySelector("#load-more-memories")?.addEventListener("click", (event) => loadMoreMemories(panel, event.currentTarget));
  panel.shadowRoot.querySelector("#load-more-conversations")?.addEventListener("click", (event) => loadMoreConversations(panel, event.currentTarget));
  panel.shadowRoot.querySelector("#guest-web-search")?.addEventListener("change", (event) => {
    if (panel._guestDraft) panel._guestDraft.guest_web_search = Boolean(event.currentTarget.checked);
  });
}
