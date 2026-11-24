import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const pagination = await import(frontend("management-history-pagination.js"));
const {loadConversationPage} = pagination;
const {syncConversationSearchOwner} = await import(frontend("management-route.js"));
// Search hits identify an exact turn, not a fixed-size page boundary. Serialized
// page budgets can return fewer than TURN_PAGE_LIMIT turns (or part of one turn).
for (const turnIndex of [19, 39, undefined]) {
  const markup = pagination.renderConversations({
    _contentData: {sessions: {sessions: [{session_id: "match", turn_index: turnIndex}]}},
    _e: value => String(value), _formatDate: value => value, _empty: value => value,
  });
  assert.deepEqual(
    [...markup.matchAll(/data-start-turn="(\d+)"/g)].map(match => Number(match[1])),
    [turnIndex ?? 0, turnIndex ?? 0],
    "Both the result card and View button must open the exact matching turn",
  );
}
assert.equal(pagination.LIST_PAGE_LIMIT, 50);
assert.equal(pagination.SEARCH_PAGE_LIMIT, 20);
assert.equal(pagination.TURN_PAGE_LIMIT, 20);
assert.equal(
  pagination.pageLabel({offset:20,returned:20,total:75,has_more:true}, "conversations"),
  "21–40 of 75 conversations",
);
assert.equal(
  pagination.pageLabel({offset:0,returned:0,total:0,has_more:false}, "matching turns"),
  "0 of 0 matching turns",
);
assert.deepEqual(
  pagination.searchSessionRows({results:[{
    session_id:"session-1",
    timestamp:"2026-09-07T12:00:00+00:00",
    title:"Remember this",
    excerpt:"matching text",
  }]}),
  [{
    session_id:"session-1",
    timestamp:"2026-09-07T12:00:00+00:00",
    title:"Remember this",
    excerpt:"matching text",
    last_message_at:"2026-09-07T12:00:00+00:00",
    turn_count:"matching",
    scope_source:"search match",
  }],
);

const [source, bootstrap, loading, permissions, management] = await Promise.all([
  readFile(frontend("management-history-pagination.js"), "utf8"),
  readFile(frontend("management-route.js"), "utf8"),
  readFile(new URL("../custom_components/extended_openai_conversation_responses/management_loading_performance.py", import.meta.url), "utf8"),
  readFile(new URL("../custom_components/extended_openai_conversation_responses/management_permissions.py", import.meta.url), "utf8"),
  readFile(new URL("../custom_components/extended_openai_conversation_responses/management_ui.py", import.meta.url), "utf8"),
]);
const manifest = JSON.parse(await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/dist/manifest.json", import.meta.url),
  "utf8",
));

assert.match(source, /next_offset/);
assert.match(source, /Previous turns/);
assert.match(source, /Next turns/);
assert.match(source, /start_turn:/);
assert.match(source, /limit: TURN_PAGE_LIMIT/);
assert.match(source, /Clear search/);
assert.match(bootstrap, /"data-memory\/conversations": \(\) => import\(".\/management-history-pagination\.js"\)/);
const historyChunk = Object.entries(manifest).find(([asset]) =>
  asset.endsWith("/management-history-pagination.js")
);
assert.ok(historyChunk, "History pagination must be emitted as a production lazy chunk");
assert.equal(historyChunk[1].isDynamicEntry, true);
assert.deepEqual(historyChunk[1].imports || [], [],
  "History list chunk must not import the broad memory browser or configuration editor");
// Backend ownership changes must not bypass the same bounded projections.
assert.match(management, /"overview": async_overview_command/);
assert.match(management, /"usage": async_usage_command/);
assert.match(management, /"conversations": async_conversations_command/);
for (const query of ["archive_list_page", "archive_search_page", "archive_get_page", "archive_get_before_page"]) {
  assert.ok(management.includes(`return await ${query}(`), `${query} remains the History read owner`);
}
assert.match(management, /result = usage_summary\(usage\)/);
assert.match(loading, /usage = usage_summary\(usage_result\)/);
assert.match(management, /require_management_permission\(is_admin, message\)/);
assert.match(permissions, /section == "usage" and action != "summary"/);
assert.doesNotMatch(management, /wrap_management_history_bounds|install_management_history_bounds/);
assert.doesNotMatch(permissions, /wrap_management_permissions/);


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return {promise, resolve, reject};
}

function historyPanel() {
  const calls = [];
  return {
    _agentId: "agent-1",
    _scopeId: "shared",
    _eocHistoryMode: "search",
    _eocHistoryQuery: "solar",
    _contentData: {sessions: {}},
    _viewKey: () => "data-memory/conversations",
    _call(_section, action, extra) {
      const request = deferred();
      calls.push({action, extra, request});
      return request.promise;
    },
    _render() {},
    _toast() {},
    calls,
  };
}

{
  class Element {
    listeners = {};
    children = [];
    dataset = {};
    style = {};
    value = "";
    setAttribute() {}
    addEventListener(name, callback) { this.listeners[name] = callback; }
    append(...children) { this.children.push(...children); }
    contains() { return false; }
    remove() {}
    closest() { return card; }
    querySelector() { return null; }
  }
  const card = new Element();
  let input = new Element();
  const originalDocument = globalThis.document;
  globalThis.document = {createElement: () => new Element()};
  try {
    const panel = historyPanel();
    panel.shadowRoot = {querySelector: selector => selector === "#archive-query" ? input : null,
      querySelectorAll: () => [], activeElement: input};
    panel._render = () => pagination.decorateConversationPager(panel);
    syncConversationSearchOwner(panel);
    pagination.bindConversationActions(panel);
    input.value = "solar";
    const submitted = pagination.searchArchive(panel);
    input.value = "  unsent text  ";
    input.listeners.input();
    panel._render();
    assert.equal(input.value, "  unsent text  ", "focused draft");
    panel.shadowRoot.activeElement = null;
    panel.calls[0].request.resolve({results: [], has_more: true, next_offset: 20});
    await submitted;
    assert.equal(input.value, "  unsent text  ", "asynchronous page completion preserves blurred draft");
    input = new Element();
    panel._render();
    assert.equal(input.value, "  unsent text  ", "replacement node restores draft");
    const next = card.children.at(-1).children.find(item => item.textContent === "Next");
    next.listeners.click();
    assert.equal(panel.calls[1].extra.query, "solar");
    panel.calls[1].request.resolve({results: [], has_more: false});
    await new Promise(resolve => setImmediate(resolve));
    const clear = card.children.at(-1).children.find(item => item.textContent === "Clear search");
    clear.listeners.click();
    assert.equal(input.value, "");
    assert.equal(panel.calls[2].action, "list");
    panel.calls[2].request.resolve({sessions: []});
    await new Promise(resolve => setImmediate(resolve));
    for (const change of [
      () => { panel._scopeId = "user:new"; },
      () => { panel._agentId = "agent-2"; },
      () => { panel._eocAuthenticationGeneration = 1; },
      () => { panel._viewKey = () => "overview"; },
    ]) {
      panel._eocHistoryDraft = "private text";
      change();
      syncConversationSearchOwner(panel);
      assert.equal(panel._eocHistoryDraft, "");
    }
  } finally {
    globalThis.document = originalDocument;
  }
}

{
  const panel = historyPanel();
  const first = loadConversationPage(panel, 0);
  panel._eocHistoryQuery = "boiler";
  const second = loadConversationPage(panel, 0);
  assert.equal(panel.calls.length, 2, "new searches must not be dropped while one is pending");
  assert.equal(panel.calls[0].extra.query, "solar");
  assert.equal(panel.calls[1].extra.query, "boiler");

  panel.calls[1].request.resolve({
    results: [{session_id: "boiler-result", timestamp: "2026-10-05T00:00:00+00:00"}],
    offset: 0, returned: 1, total: 1, has_more: false,
  });
  await second;
  panel.calls[0].request.resolve({
    results: [{session_id: "solar-result", timestamp: "2026-10-04T23:00:00+00:00"}],
    offset: 0, returned: 1, total: 1, has_more: false,
  });
  await first;

  assert.equal(panel._eocHistoryQuery, "boiler");
  assert.equal(panel._contentData.sessions.sessions[0].session_id, "boiler-result",
    "a stale search response must not replace the latest query results");
}

{
  const panel = historyPanel();
  const originalTarget = panel._contentData;
  const pending = loadConversationPage(panel, 0);
  panel._scopeId = "user:new";
  panel._contentData = {
    sessions: {sessions: [{session_id: "new-scope"}]},
  };
  panel.calls[0].request.resolve({
    results: [{session_id: "old-scope", timestamp: "2026-10-04T23:00:00+00:00"}],
    offset: 0, returned: 1, total: 1, has_more: false,
  });
  await pending;

  assert.notEqual(panel._contentData, originalTarget);
  assert.equal(panel._contentData.sessions.sessions[0].session_id, "new-scope",
    "a late response from the previous scope must not overwrite the new scope");
}

class TurnElement {
  children = [];
  style = {};
  listeners = {};
  attributes = {};
  html = "";
  append(item) { this.children.push(item); item.parent = this; }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter(item => item !== this);
    this.parent = null;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set innerHTML(value) {
    this.html = value;
    this.children.forEach(item => { item.parent = null; });
    this.children = [];
  }
  get innerHTML() { return this.html; }
  click() { if (!this.disabled) this.listeners.click?.(); }
}
globalThis.document = {createElement: () => new TurnElement()};
const tick = () => new Promise(resolve => setImmediate(resolve));
const sessionControls = body => label => body.children.flatMap(item => item.children).find(item => item.textContent === label);

// A failed load can retry its cursor, including one within a partial turn.
{
  const body = new TurnElement();
  const title = new TurnElement();
  const dialog = {open: false, showModal() { this.open = true; }};
  const requests = [];
  let failNext = true;
  const panel = {
    _agentId: "agent", _scopeId: "scope",
    shadowRoot: {querySelector: selector => ({"#session-dialog": dialog, "#session-body": body, "#session-title": title})[selector]},
    _ensureOwnedDialog() {}, _loading: () => "loading", _e: value => String(value),
    _formatDate: value => value, _empty: value => value,
    async _call(section, action, cursor) {
      requests.push(cursor);
      if (failNext) {
        failNext = false;
        throw new Error("Retryable outage");
      }
      const startTurn = action === "get_before" ? cursor.before_turn - 1 : cursor.start_turn;
      const textOffset = action === "get_before" ? 0 : cursor.text_offset;
      return {
        session: {title: "Long conversation"}, start_turn: startTurn,
        text_offset: textOffset, offset: startTurn, returned: 1, total: 1,
        turns: [{user_text: "fragment " + textOffset, assistant_text: "", timestamp: "now"}],
        has_more: textOffset === 0, next_start_turn: 0, next_text_offset: 30000,
      };
    },
  };
  const button = sessionControls(body);
  await pagination.openSession(panel, "session");
  assert.equal(title.textContent, "Unable to load conversation");
  assert.equal(body.children[0].children[0].attributes.role, "alert");
  assert.match(body.children[0].children[0].textContent, /Retryable outage/);
  button("Retry loading turns").click();
  await tick();
  assert.deepEqual(requests.at(-1), requests[0], "Initial retry asks for the original cursor");
  assert.equal(button("Previous turns").disabled, true);
  assert.equal(button("Start of conversation"), undefined);
  assert.equal(button("Retry loading turns"), undefined);
  const originalPage = body.innerHTML;
  const originalNext = button("Next turns");
  failNext = true;
  originalNext.click();
  assert.equal(body.innerHTML, originalPage, "A pending page keeps the last transcript");
  originalNext.click();
  await tick();
  assert.equal(requests.length, 3, "Repeated clicks do not enqueue the same page");
  assert.equal(body.innerHTML, originalPage, "A failed page keeps the last transcript");
  assert.equal(title.textContent, "Long conversation");
  assert.equal(button("Next turns"), originalNext);
  assert.equal(requests.at(-1).start_turn, 0, "Next must stay within the long turn");
  assert.equal(requests.at(-1).text_offset, 30000);
  button("Retry loading turns").click();
  await tick();
  assert.deepEqual(requests.at(-1), requests.at(-2));
  assert.match(body.innerHTML, /fragment 30000/);
  assert.equal(button("Retry loading turns"), undefined);
  assert.ok(button("Start of conversation"), "Long-turn fragments can return to the beginning");
  assert.equal(button("Previous turns").disabled, false, "Previous uses actual cursor history even at turn zero");
  button("Previous turns").click();
  await tick();
  assert.equal(requests.at(-1).start_turn, 0);
  assert.equal(requests.at(-1).text_offset, 0);
  await pagination.openSession(panel, "another-session", 40);
  assert.equal(button("Previous turns").disabled, false, "Search hits allow backward paging without prior visits");
  button("Previous turns").click();
  await tick();
  assert.equal(requests.at(-1).before_turn, 40);
  assert.equal(requests.at(-1).before_text_offset, 0);
  button("Start of conversation").click();
  await tick();
  assert.equal(requests.at(-1).session_id, "another-session");
  assert.equal(requests.at(-1).start_turn, 0);
  assert.equal(requests.at(-1).text_offset, 0);
  assert.equal(button("Previous turns").disabled, true);
  assert.equal(button("Start of conversation"), undefined);
}

// A secondary active-conversation read replaces the container, not the retained
// session snapshot. The user's pending search must still settle into that view.
{
  const panel = historyPanel();
  const pending = loadConversationPage(panel, 0);
  panel._contentData = {...panel._contentData, active:{active:[]}};
  panel.calls[0].request.resolve({results:[], offset:0, returned:0, total:0, has_more:false});
  await pending;
  assert.equal(panel._eocHistoryResult.mode, "search");
  assert.equal(panel._eocHistoryResult.sessions, panel._contentData.sessions);
  panel._contentData = {...panel._contentData, loading:{active:false}};
  assert.equal(panel._eocHistoryResult.sessions, panel._contentData.sessions);
}
{
  const panel = historyPanel();
  const pending = loadConversationPage(panel, 0);
  panel._eocAuthenticationGeneration = 2;
  panel.calls[0].request.resolve({results:[{session_id:"retired"}], total:1});
  await pending;
  assert.equal(panel._eocHistoryResult, undefined);
  assert.deepEqual(panel._contentData.sessions, {});
}


// Search hit -> earlier page -> yet earlier -> forward twice -> previous.
// Refetch must preserve the original exact backward page's end cursor.
{
  const body = new TurnElement(), title = new TurnElement();
  const dialog = {open: false, showModal() { this.open = true; }};
  const calls = [];
  let failBefore = true;
  const panel = {
    _agentId: "agent", _scopeId: "user:alice",
    shadowRoot: {querySelector: selector => ({
      "#session-dialog": dialog, "#session-body": body, "#session-title": title,
    })[selector]},
    _ensureOwnedDialog() {}, _loading: () => "loading",
    _e: String, _formatDate: String, _empty: String,
    async _call(section, action, cursor) {
      calls.push({section, action, ...cursor});
      if (action === "get_before") {
        if (failBefore) {
          failBefore = false;
          throw new Error("Retry backward page");
        }
        const end = cursor.before_turn;
        return {
          session: {title: "Archive"}, start_turn: end - 1, text_offset: 0,
          offset: end - 1, returned: 1, total: 100, has_more: true,
          next_start_turn: end, next_text_offset: 0,
          turns: [{user_text: "turn " + (end - 1), assistant_text: "", timestamp: "now"}],
        };
      }
      return {
        session: {title: "Archive"}, start_turn: cursor.start_turn,
        text_offset: cursor.text_offset, offset: cursor.start_turn,
        returned: 1, total: 100, has_more: cursor.start_turn < 99,
        next_start_turn: cursor.start_turn + 1, next_text_offset: 0,
        turns: [{user_text: "turn " + cursor.start_turn, assistant_text: "", timestamp: "now"}],
      };
    },
  };
  const button = sessionControls(body);
  const click = async label => {
    button(label).click();
    await tick();
  };
  await pagination.openSession(panel, "match", 85);
  assert.equal(button("Previous turns").disabled, false);
  await click("Previous turns");
  assert.equal(calls.at(-1).action, "get_before");
  assert.equal(calls.at(-1).before_turn, 85);
  assert.match(body.html, /turn 85/);
  await click("Retry loading turns");
  assert.deepEqual(calls.at(-1), calls.at(-2), "Backward retry retains its direction and exact end cursor");
  assert.match(body.html, /turn 84/);
  await click("Previous turns");
  assert.equal(calls.at(-1).before_turn, 84);
  assert.match(body.html, /turn 83/);
  await click("Next turns");
  assert.equal(calls.at(-1).action, "get_before");
  assert.equal(calls.at(-1).before_turn, 85);
  assert.match(body.html, /turn 84/);
  await click("Next turns");
  assert.equal(calls.at(-1).action, "get");
  assert.equal(calls.at(-1).start_turn, 85);
  assert.match(body.html, /turn 85/);
  await click("Previous turns");
  assert.equal(calls.at(-1).before_turn, 85);
  await click("Start of conversation");
  assert.equal(calls.at(-1).action, "get");
  assert.equal(calls.at(-1).start_turn, 0);
  assert.equal(button("Previous turns").disabled, true);
}

// Retry controls and pending failures stay with the exact dialog owner.
for (const retire of [
  ({dialog}) => { dialog.open = false; },
  ({panel}) => { panel._scopeId = "user:bob"; },
  ({panel}) => { panel._agentId = "other-agent"; },
  ({panel}) => { panel._eocAuthenticationGeneration = 1; },
  ({panel}) => { panel.shadowRoot.querySelector = () => null; },
]) {
  const body = new TurnElement(), title = new TurnElement();
  const dialog = {open: false, showModal() { this.open = true; }};
  const calls = [];
  const panel = {
    _agentId: "agent", _scopeId: "user:alice",
    shadowRoot: {querySelector: selector => ({"#session-dialog": dialog, "#session-body": body, "#session-title": title})[selector]},
    _ensureOwnedDialog() {}, _loading: () => "loading",
    _call() {
      const request = deferred();
      calls.push(request);
      return request.promise;
    },
  };
  const first = pagination.openSession(panel, "session");
  calls[0].reject(new Error("Initial error"));
  await first;
  const retry = sessionControls(body)("Retry loading turns");
  retry.click();
  assert.equal(calls.length, 2);
  retire({panel, dialog});
  calls[1].reject(new Error("Late failure"));
  await tick();
  assert.equal(sessionControls(body)("Retry loading turns"), undefined);
  retry.click();
  assert.equal(calls.length, 2, "A retired retry cannot read for a different owner");
}
