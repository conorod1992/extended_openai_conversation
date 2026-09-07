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

const [source, bootstrap, runtime, permissions] = await Promise.all([
  readFile(frontend("management-history-pagination.js"), "utf8"),
  readFile(frontend("management-bootstrap.js"), "utf8"),
  readFile(new URL("../custom_components/extended_openai_conversation_responses/management_history_runtime.py", import.meta.url), "utf8"),
  readFile(new URL("../custom_components/extended_openai_conversation_responses/management_permissions.py", import.meta.url), "utf8"),
]);

assert.match(source, /next_offset/);
assert.match(source, /Previous turns/);
assert.match(source, /Next turns/);
assert.match(source, /start_turn:/);
assert.match(source, /limit: TURN_PAGE_LIMIT/);
assert.match(source, /Clear search/);
assert.match(bootstrap, /await import\("\.\/management-history-pagination\.js"\)/);
assert.match(runtime, /_FRONTEND_MODULE = "management-history-pagination\.js"/);
assert.match(runtime, /section not in \{"overview", "usage", "conversations"\}/);
assert.match(runtime, /result = \{\*\*result, "usage": usage_summary\(usage\)\}/);
assert.match(permissions, /install_management_history_bounds\(\)/);
assert.match(permissions, /wrap_management_permissions/);
