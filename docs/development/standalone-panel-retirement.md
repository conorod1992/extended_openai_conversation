# Standalone Memory and Knowledge panel audit

Audited `develop` at `7232aee041ebda074809c39ebb47132e794c4404` for the
second post-PR5 simplification PR. This is a **partial retirement**: Knowledge
can be removed; Memory needs a separate parity decision before removal.

## Production reachability

- `__init__.py:async_setup` registers Debug and unified Management UI only.
  Neither `async_setup_memory_ui` nor `async_setup_knowledge_ui` has a production
  caller. Their WebSocket commands, static assets and sidebar panels are not
  registered by normal startup.
- `knowledge_ui.py` has no production importer or command caller. Its only
  callers are `test_knowledge_ui.py`, `test_knowledge.py` and
  `test_knowledge_availability.py`.
- `memory_ui.py` is imported by
  `agent_maintenance._install_legacy_memory_guard`, called during startup. This
  wraps `async_manage_command` but does not register it or invoke it. All actual
  command calls are in tests. Keep this guard while retaining Memory; unrelated
  maintenance wrappers are outside this retirement.
- The three standalone frontend files are referenced only by their own setup
  functions, the Memory subclass import, and legacy tests. No unified frontend
  import or static registration consumes them. Their custom elements are not
  used by Management.
- The four standalone URL/title constants are used only by their respective
  legacy modules. Their `WS_COMMAND` and `_UI_SETUP` values are module-local.
- Options-flow discovery still contains literal links to the unregistered
  standalone paths. Point these forms to the existing unified subsections.
  This repairs discovery; it does not add a redirect or register an old route.

## Feature comparison

| Capability | Current unified Management | Decision |
| --- | --- | --- |
| Agent enumeration and Memory mode | `agents`, including mode/count; scope catalog | Retained |
| Persistent list/search/pagination | `management_browser` wraps the Memory list/search actions, with bounded pages and `has_more` | Parity |
| Persistent add/update/delete/clear | `management_ui`, selected scope authorization, confirmation for clear | Basic CRUD parity |
| Personal/shared/legacy scopes | `_selected_scope`, `_memory_scope`, scope catalog; admin-only legacy reassignment | Retained; authorization differs deliberately |
| Persistent metadata edits and scope moves | Unified editor sends content/category only; legacy supports importance, subject, key, valid-from, clear-fields and moving ownership | **Gap: retain Memory** |
| Revision and confirmation semantics | Legacy serializes revisions and passes `expected_revision` and `refresh_confirmation=False`; unified list/editor/update do not | **Gap: retain Memory** |
| Temporary list/update/delete | `temporary_memory_ownership` wraps unified actions using validated owner scopes | Retained; unified adds editing |
| Temporary bulk clear | Legacy confirms and deletes the authenticated user's records in batches; no unified `temporary_clear` action/control | **Gap: retain Memory** |
| Agent testing | Unified diagnostics `test_agent`, plus native options flow | Parity; current admin restriction retained |
| Knowledge list/get/create/update/delete | Unified `knowledge` section calls the same core library | Parity |
| Knowledge source enabled state | Unified create/update, list/get, editor and existing availability tests | Parity |
| Knowledge stats/source count | List returns `stats`; agents/overview expose source count | Parity |
| Knowledge permissions | Legacy accepts authenticated users; current `management_permissions` intentionally requires admin for the entire section | Preserve current boundary; do not revive the weaker endpoint |

The Memory gaps are useful data-editing and stale-write safeguards, not merely
historical payload differences. Moving them would require changes to the live
editor, list serialization and backend contract. Retain `memory_ui.py`, both
Memory frontend files, their constants, maintenance guard and all their tests
for a separate bounded parity change. This PR does not claim complete Memory
retirement or fix these pre-existing gaps.

## Compatibility audit

Repository docs, tests, comments, published GitHub release notes and public issue/
PR search were checked. No external WebSocket API stability commitment was found
for `/manage` or `/knowledge` under the integration domain. The old UI methods
describe integration-owned authenticated management surfaces. The original
Knowledge PR (#12) describes a management API/panel, without an external API or
route compatibility guarantee.

Stale usage guidance does exist: Knowledge docs advertise
`/extended-openai-knowledge`, configuration docs name a Knowledge sidebar panel,
and troubleshooting names an OpenAI memories sidebar panel. Update these to the
unified Management UI. The documented `/knowledge` and `/memories` aliases *inside*
`/extended-openai` are different routes and remain supported and unchanged.
No evidence of external consumers was found; this cannot prove absence of private
custom clients. Normal startup already leaves both old WebSocket types and old
sidebar URLs unregistered before this PR.

## Removal and coverage

Delete `knowledge_ui.py`, `frontend/knowledge-panel.js`, the Knowledge URL/title
constants, and `tests/test_knowledge_ui.py` (legacy-only dispatch, schema, setup
and transport tests). Their registration functions, local setup flag and
WebSocket constant disappear with the module.

Migrate the real-library CRUD/content-omission/confirmation tests in
`test_knowledge.py` to Management. Consolidate the duplicate old availability
test into the existing unified availability test, retaining disabled-source list
coverage. Existing Management tests cover entry resolution, enumeration, dispatch,
WebSocket errors and idempotent registration. Add genuine-HA acceptance that old
command types are unregistered, unified Memory/Knowledge calls work, Knowledge
CRUD survives, and normal users cannot access Knowledge.

Keep `test_memory_ui_and_modes.py` intact: it covers core mode normalization,
migration and UI behavior. Keep all other Memory tests, including the core stale
revision and blank-update regressions in `test_memory_management_parity.py`.
Core stores, model tools, backups, Guest Mode, runtime, services and diagnostics
are not changed.
