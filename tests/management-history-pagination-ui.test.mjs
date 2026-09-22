import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const pagination = await import(frontend("management-history-pagination.js"));
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
