# Standalone Memory and Knowledge panel retirement

Memory parity is complete in unified Management. The dormant Memory stack is now
removed, following the earlier Knowledge retirement. The prior reachability audit
still applies: normal startup already registered only unified Management and Debug;
this change does not remove a reachable production endpoint or add a redirect.

## Memory parity delivered

- Persistent editing supports content, category, importance, subject, key and
  valid-from, including explicit clearing of optional metadata. Source, creation
  timestamps and raw confirmation timestamps remain read-only.
- Personal/shared moves use `PersistentMemory.async_update(target_user_id=...)`.
  Both scopes pass current Management authorization. Shared scopes remain admin-only;
  moving into disabled shared memory is rejected. The same Memory ID survives the
  atomic move. Failed persistence rolls back through the existing store boundary.
- Management list/search/update projections expose `memory_revision`; editors retain
  their original token and send `expected_revision`. Conflicts leave the draft open
  and the store unchanged. Cancel, refresh the list, and reopen to use fresh data.
- Ordinary edits explicitly preserve confirmation age. The deliberate "Refresh
  confirmation on save" checkbox opts into the store's existing refresh operation.
  Confirmation-only changes retain the existing substantive revision semantics.
- Temporary bulk clear confirms first and clears all records in the selected
  Personal/Shared owner scope and agent, regardless of search. It uses owned delete
  batches of at most 50. Persistent records are unaffected.

Only the Management adapter, its WebSocket schema and projection were extended.
No shared store/model-tool/service API or storage format was removed or redesigned.
Existing optional-revision compatibility remains for other API callers; the unified
editor refuses an existing-record save without its captured revision.

## PR3 preservation

The existing lazy Memory route owns metadata helpers and clear actions. Mutations
use the existing authoritative refresh and keyed reconciliation. Browser assertions
cover unrelated card identity, no main replacement, retained search, one delegated
handler, open drafts and stale responses across scope/agent/kind boundaries. The
Memory metadata fields mount inside the existing persistent dialog on first use.
There is no global render invalidation change and no measured latency claim.

## Regression classification and migration

| Original tests | Classification | Disposition |
| --- | --- | --- |
| `test_memory_ui.py`: legacy owner lookup, agents/test dispatch, setup and transport/schema shape | Standalone-surface-only | Removed with the module. Live entry/agent resolution, dispatcher, WebSocket and setup coverage remains in Management tests. New schema and retirement assertions cover the live surface. |
| `test_memory_ui.py`: metadata forwarding, control validation, household permission, scoped delete/clear, paging | Parity/safety | Migrated to `test_memory_management_parity.py`; complete paging/search remains in `test_management_browser.py` and its coverage companion; CRUD/confirmed clear remains in `test_memory_ui_and_modes.py`. Current Management authorization is preserved rather than the dormant endpoint's weaker shared permissions. |
| `test_memory_ui_and_modes.py`: mode normalization, config flow, version migration | Shared backend regression | Preserved unchanged. The two CRUD/confirmation tests now call unified Management. |
| `test_memory_management_parity.py`: stale revision and blank update | Shared backend regression | Retained against the real `PersistentMemory` store. |
| `test_memory_management_parity.py`: temporary isolation/clear, metadata and owner resolution | Parity/safety | Replaced legacy adapter fixtures with live Management/store tests for authorized source/target, both move directions, failed durable moves, conflicts, metadata clearing and confirmed batched clear. |
| `test_memory_management_parity.py`: legacy agent response and standalone source assertion | Standalone-surface-only | Removed; live catalog and temporary owner validation have dedicated tests. |
| `test_memory_management_confirmation.py` | Parity/safety | Same ordinary-edit assertion migrated to unified dispatch; explicit refresh and substantive revision tests added. |
| `test_safety_hardening_startup.py` | Startup/non-registration | Retained; stable method ownership now targets `async_memories_command`. |
| `memory-management-parity-ui.test.mjs` | Parity/safety | Migrated to executable unified payload checks and revision-snapshot assertions. |
| Standalone cases in `persistent-data-collections.spec.mjs` | Parity/safety and standalone-only controls | Metadata, ownership, clear, revision/editor and identity assertions migrated to `memory-management-parity.spec.mjs`; unified search, stale pagination/agent/scope and delete assertions retained. Removed only standalone chip/layout/filter/setup fixtures. |
| Genuine-HA old `/manage` acceptance | Startup/non-registration | Retained; adds absent old panel/static registrations and rich Memory WebSocket edit/conflict coverage. |
| `test_memory_retirement.py` | Startup/non-registration | New explicit file/build/reference and idempotent live-registration guards. |

## Removed surface and packaging

Removed `memory_ui.py`, `frontend/memory-panel.js`, and
`frontend/memory-management-panel.js`, including their local registration/setup
helpers, `/manage` command schema, and both standalone custom elements. Removed
`MEMORY_PANEL_URL` and `MEMORY_PANEL_TITLE`, the standalone browser fixture, and
its helper/benchmark branches. No maintenance importer remains on current develop.

Release staging enumerates tracked integration files with `git ls-files`; removed
files are no longer in that payload. No Vite entry, static registration or dynamic
import references the standalone files. Production assets are regenerated using
`frontend`'s normal Vite build, never edited by hand. Historical PR3 measurement
JSON remains as evidence of that earlier run; it is not a shipped surface.

Genuine Home Assistant acceptance requires Linux CI. Local mock-backed Python
checks can run on Windows with pytest plugin autoload disabled and the asyncio
plugin explicitly enabled; that is not a substitute for the genuine-HA jobs.

## Validation for the Memory retirement

- Frontend TypeScript check, 31 Vitest tests, all 77 standalone JS tests, and the
  normal Vite production build passed.
- 67 distinct Chromium checks passed across parity, persistent collections, lazy
  ownership, CRUD and unified saving suites (66-test run plus the final added
  confirmation-boundary test; the changed collection suites were rerun together).
- 462 Memory/Management/Temporary Memory/frontend-registration/startup Python tests
  passed with Windows-compatible plugin selection. The final focused parity and
  retirement rerun also passed (21 tests). Native full-plugin collection fails on
  Linux-only `fcntl`; no Linux distribution is installed locally. Genuine-HA tests
  remain for Linux CI, including the extended WebSocket/registration acceptance.
- Ruff, Python compilation, JS syntax, and diff checks passed. A release ZIP staged
  from the tracked integration inventory contains 255 files and no retired assets.
- Removed standalone source: 1,832 lines / 67,581 bytes across three files, including
  two raw frontend assets (52,553 bytes). Including replacement code and the two
  constants, production source shrinks by 1,683 lines, excluding generated bundles.
