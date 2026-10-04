import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const pagination = await import(frontend("management-history-pagination.js"));
const {loadConversationPage} = pagination;
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
