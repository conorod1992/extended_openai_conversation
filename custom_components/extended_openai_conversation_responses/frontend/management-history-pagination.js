const CONVERSATIONS_VIEW = "data-memory/conversations";
const LIST_PAGE_LIMIT = 50;
const SEARCH_PAGE_LIMIT = 20;
const TURN_PAGE_LIMIT = 20;

function integer(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)) : fallback;
}

function pageLabel(meta = {}, noun = "items") {
  const offset = integer(meta.offset);
  const returned = integer(meta.returned, Array.isArray(meta.sessions) ? meta.sessions.length : 0);
  const total = Number.isFinite(Number(meta.total)) ? integer(meta.total) : null;
  if (!returned) return total === null ? `No ${noun}` : `0 of ${total.toLocaleString()} ${noun}`;
  const range = `${(offset + 1).toLocaleString()}–${(offset + returned).toLocaleString()}`;
  return total === null ? `${range} ${noun}` : `${range} of ${total.toLocaleString()} ${noun}`;
}

function searchSessionRows(found) {
  return (found?.results || []).map((item) => ({
    ...item,
    last_message_at: item.timestamp,
    turn_count: "matching",
    scope_source: "search match",
  }));
}

function conversationResult(panel) {
  return panel._contentData || panel._result || {};
}

function setSessionResult(panel, response, mode) {
  const target = conversationResult(panel);
  target.sessions = mode === "search"
    ? {...response, sessions: searchSessionRows(response)}
    : response;
}

async function loadConversationPage(panel, offset) {
  if (panel._eocHistoryPagePending) return;
  panel._eocHistoryPagePending = true;
  try {
    const mode = panel._eocHistoryMode === "search" ? "search" : "list";
    const extra = {
      scope_id: panel._scopeId,
      offset: integer(offset),
      limit: mode === "search" ? SEARCH_PAGE_LIMIT : LIST_PAGE_LIMIT,
    };
    if (mode === "search") extra.query = panel._eocHistoryQuery || "";
    const response = await panel._call("conversations", mode, extra);
    setSessionResult(panel, response, mode);
    panel._render();
  } catch (err) {
    panel._toast(`Unable to load conversation history: ${err.message || String(err)}`, true);
  } finally {
    panel._eocHistoryPagePending = false;
  }
}

function decorateConversationPager(panel) {
  if (panel._viewKey?.() !== CONVERSATIONS_VIEW) return;
  const input = panel.shadowRoot?.querySelector("#archive-query");
  const card = input?.closest(".content-card");
  if (!input || !card) return;
  input.value = panel._eocHistoryMode === "search" ? (panel._eocHistoryQuery || "") : "";

  const sessions = conversationResult(panel).sessions || {};
  const existing = card.querySelector(".eoc-history-pager");
  existing?.remove();
  const pager = document.createElement("div");
  pager.className = "section-actions eoc-history-pager";

  const status = document.createElement("small");
  status.className = "meta";
  status.style.marginRight = "auto";
  status.textContent = pageLabel(
    sessions,
    panel._eocHistoryMode === "search" ? "matching turns" : "conversations",
  );
  pager.append(status);

  if (panel._eocHistoryMode === "search") {
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "secondary";
    clear.textContent = "Clear search";
    clear.addEventListener("click", () => {
      panel._eocHistoryMode = "list";
      panel._eocHistoryQuery = "";
      void loadConversationPage(panel, 0);
    });
    pager.append(clear);
  }

  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "secondary";
  previous.textContent = "Previous";
  const offset = integer(sessions.offset);
  const limit = Math.max(1, integer(sessions.limit, panel._eocHistoryMode === "search" ? SEARCH_PAGE_LIMIT : LIST_PAGE_LIMIT));
  previous.disabled = offset === 0 || panel._eocHistoryPagePending;
  previous.addEventListener("click", () => void loadConversationPage(panel, Math.max(0, offset - limit)));
  pager.append(previous);

  const next = document.createElement("button");
  next.type = "button";
  next.className = "secondary";
  next.textContent = "Next";
  next.disabled = !sessions.has_more || panel._eocHistoryPagePending;
  const nextOffset = sessions.next_offset == null
    ? offset + integer(sessions.returned, (sessions.sessions || []).length)
    : integer(sessions.next_offset);
  next.addEventListener("click", () => void loadConversationPage(panel, nextOffset));
  pager.append(next);

  card.append(pager);
}

function renderTurns(panel, data) {
  return (data.turns || []).map((turn) => `<article class="turn"><div class="message user"><strong>You</strong><p>${panel._e(turn.user_text)}</p></div><div class="message assistant"><strong>Assistant</strong><p>${panel._e(turn.assistant_text)}</p></div><small>${panel._e(panel._formatDate(turn.timestamp))}</small></article>`).join("") || panel._empty("No turns retained on this page.");
}

function appendTurnPager(panel, body, sessionId, data) {
  const pager = document.createElement("div");
  pager.className = "section-actions eoc-turn-pager";
  const status = document.createElement("small");
  status.className = "meta";
  status.style.marginRight = "auto";
  status.textContent = pageLabel(data, "turns");
  pager.append(status);

  const offset = integer(data.offset, integer(data.start_turn));
  const limit = Math.max(1, integer(data.limit, TURN_PAGE_LIMIT));
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "secondary";
  previous.textContent = "Previous turns";
  previous.disabled = offset === 0;
  previous.addEventListener("click", () => void openSession(panel, sessionId, Math.max(0, offset - limit)));
  pager.append(previous);

  const next = document.createElement("button");
  next.type = "button";
  next.className = "secondary";
  next.textContent = "Next turns";
  next.disabled = !data.has_more;
  const nextOffset = data.next_offset == null
    ? offset + integer(data.returned, (data.turns || []).length)
    : integer(data.next_offset);
  next.addEventListener("click", () => void openSession(panel, sessionId, nextOffset));
  pager.append(next);
  body.append(pager);
}

export function bindConversationActions(panel) {
  const root = panel.shadowRoot;
  const host = root.querySelector("#archive-query")?.closest(".content-card");
  if (!host || host.__eocHistoryActionsBound) return;
  host.__eocHistoryActionsBound = true;
  root.querySelectorAll(".open-session, .view-session").forEach(element => panel._activate(element, () => openSession(panel, element.dataset.id)));
  root.querySelector("#archive-search")?.addEventListener("click", () => searchArchive(panel));
  root.querySelector("#archive-query")?.addEventListener("keydown", event => { if (event.key === "Enter") void searchArchive(panel); });
}

async function searchArchive(panel, offset = 0) {
  const input = panel.shadowRoot.querySelector("#archive-query");
  const query = (offset ? panel._eocHistoryQuery : input?.value || "").trim();
  if (!query) {
    panel._eocHistoryMode = "list";
    panel._eocHistoryQuery = "";
    await loadConversationPage(panel, 0);
    return;
  }
  panel._eocHistoryMode = "search";
  panel._eocHistoryQuery = query;
  await loadConversationPage(panel, offset);
}

async function openSession(panel, sessionId, startTurn = 0) {
  panel._ensureOwnedDialog("session-dialog");
  const root = panel.shadowRoot;
  const dialog = root.querySelector("#session-dialog");
  const title = root.querySelector("#session-title");
  const body = root.querySelector("#session-body");
  const token = (panel._eocSessionLoadToken || 0) + 1;
  panel._eocSessionLoadToken = token;
  title.textContent = "Loading conversation…";
  body.innerHTML = panel._loading();
  if (!dialog.open) dialog.showModal();
  try {
    const data = await panel._call("conversations", "get", {
      scope_id: panel._scopeId,
      session_id: sessionId,
      start_turn: integer(startTurn),
      limit: TURN_PAGE_LIMIT,
    });
    if (panel._eocSessionLoadToken !== token || !dialog.open) return;
    title.textContent = data.session?.title || "Untitled conversation";
    body.innerHTML = renderTurns(panel, data);
    appendTurnPager(panel, body, sessionId, data);
  } catch (err) {
    if (panel._eocSessionLoadToken !== token || !dialog.open) return;
    title.textContent = "Unable to load conversation";
    body.innerHTML = `<div class="error" role="alert">${panel._e(err.message || String(err))}</div>`;
  }
}

export {
  decorateConversationPager,
  LIST_PAGE_LIMIT,
  SEARCH_PAGE_LIMIT,
  TURN_PAGE_LIMIT,
  pageLabel,
  searchSessionRows,
};
