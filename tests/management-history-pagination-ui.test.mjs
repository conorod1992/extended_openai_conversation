import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const pagination = await import(frontend("management-history-pagination.js"));
const {loadConversationPage} = pagination;
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
for (const query of ["archive_list_page", "archive_search_page", "archive_get_page"]) {
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

// Exercise real dialog navigation: a page can continue within the same turn.
{
  class Element {
    constructor() { this.children = []; this.style = {}; this.listeners = {}; this._html = ""; }
    append(element) { this.children.push(element); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    set innerHTML(value) { this._html = value; this.children = []; }
    get innerHTML() { return this._html; }
    click() { if (!this.disabled) this.listeners.click?.(); }
  }
  globalThis.document = {createElement: () => new Element()};
  const body = new Element();
  const title = new Element();
  const dialog = {open: false, showModal() { this.open = true; }};
  const requests = [];
  const panel = {
    _agentId: "agent", _scopeId: "scope",
    shadowRoot: {querySelector: selector => ({"#session-dialog": dialog, "#session-body": body, "#session-title": title})[selector]},
    _ensureOwnedDialog() {}, _loading: () => "loading", _e: value => String(value),
    _formatDate: value => value, _empty: value => value,
    async _call(section, action, cursor) {
      requests.push(cursor);
      return {
        session: {title: "Long conversation"}, start_turn: cursor.start_turn,
        text_offset: cursor.text_offset, offset: cursor.start_turn, returned: 1, total: 1,
        turns: [{user_text: `fragment ${cursor.text_offset}`, assistant_text: "", timestamp: "now"}],
        has_more: cursor.text_offset === 0, next_start_turn: 0, next_text_offset: 30000,
      };
    },
  };
  const button = label => body.children[0].children.find(item => item.textContent === label);
  await pagination.openSession(panel, "session");
  assert.equal(button("Previous turns").disabled, true);
  assert.equal(button("Start of conversation"), undefined);
  button("Next turns").click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests[1].start_turn, 0, "Next must stay within the long turn");
  assert.equal(requests[1].text_offset, 30000);
  assert.ok(button("Start of conversation"), "Long-turn fragments can return to the beginning");
  assert.equal(button("Previous turns").disabled, false, "Previous uses actual cursor history even at turn zero");
  button("Previous turns").click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests[2].start_turn, 0);
  assert.equal(requests[2].text_offset, 0);
  await pagination.openSession(panel, "another-session", 40);
  assert.equal(button("Previous turns").disabled, true, "Opening another session resets cursor history");
  button("Start of conversation").click();
  await new Promise(resolve => setImmediate(resolve));
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
