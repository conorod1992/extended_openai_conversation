# PR3: Persistent Knowledge and Memory collections

## Architecture and authority

Baseline: `develop` at `202cfa006d3a98f021e4299c9644e6113bd42f4a` (merged PR2).

Knowledge now has a lazy route owner, `management-knowledge-feature.js`. It owns source presentation, filtering, delegated collection actions, and keyed reconciliation. The existing `panel._reconcileCollectionView()` hook dispatches through `getRouteFeature`; the renderer and its editor-deferral policy are unchanged. Unchanged `source_id` cards are retained. Changed presentation replaces just that card, additions are inserted in backend order, removals prune their keys, and counts/status are updated separately.

The main Memory route remains lazy through `management-memory-feature.js`. `guest-mode-ui.js` owns persistent-memory reconciliation and the existing paginated/debounced search; `management-temporary-memory.js` owns a separate, smaller temporary collection. Both use `memory_id`. Identity includes entry, agent, authenticated user, selected scope and kind; cross-identity transitions deliberately take the normal loading/replacement path. Search timers, requests and pagination check identity, sequence and query before accepting results. Same-scope refreshes preserve the existing read-only list while loading.

The historical measurements below also included the then-retained standalone Memory panel. That panel is now retired after unified parity; current benchmarks and browser regressions exercise unified Management only. Historical JSON is retained unchanged.

`keyed-collection.js` gains a small keyed-children convenience function, an element-returning render option for the existing DOM-built standalone cards, and a once-per-host delegated click/keyboard helper. It does not own data, loading, caching, dialogs or route lifetime. Collection actions look up current records, rather than closing over stale objects. Native buttons retain native keyboard click synthesis; card activation handles Enter/Space once. Existing mutation confirmation, validation, pending-save guards, conflict handling and backend refresh paths remain authoritative; there are no new optimistic successful edits.

## Filtering, cache refresh and editors

Knowledge and temporary-memory filters hide/show keyed cards without calling the route render path. Persistent Memory immediately filters already-known items, then performs its existing 250 ms debounced backend search. Search pages are merged into the current scoped collection, and the returned IDs determine visibility after completion. Existing cards do not disappear merely because a query changes. New matches beyond the loaded page can add cards; actual authoritative reloads can remove records no longer present. The existing pagination remains in use.

Stale Knowledge cache display and the later authoritative response use the same collection host. An unchanged response has zero element additions/removals; a response with an edit, insertion and deletion affects only those cards. An open editor continues to use the existing deferred-render mechanism: background data can arrive, but does not replace its controls or draft. Search completion likewise marks the existing deferred flag while a dialog is open. Unified Memory now retains the revision snapshot through that same dialog lifecycle.

The initial route build, agent/user/scope/kind changes, loading/error recovery, and non-isolatable conditions may still use the normal full-render fallback. Persistence is not attempted across logical collection boundaries. A changed card itself may be replaced (including its internal focus); unaffected cards, the search input, its selection/focus and the main container remain connected. Filtering still scans loaded records; this is not an O(1) CPU claim or virtualization. Search accumulates discovered records only within its current logical collection and resets on authoritative reload or identity change.

## Reproduce the structural benchmark

Install the locked frontend dependencies and the same Playwright version as CI:

```sh
npm ci --prefix frontend --no-audit --no-fund
npm install --no-save --package-lock=false --no-audit --no-fund @playwright/test@1.63.0
npx playwright install chromium
python3 -m http.server 4173 --bind 127.0.0.1
```

In another terminal at the repository root:

```sh
node tests_browser/data-collection-benchmark.mjs /tmp/data-collections-after.json
```

The script supports `BENCHMARK_BASE_URL` and `CHROMIUM_EXECUTABLE_PATH`. To measure the original implementation, create a detached worktree at the baseline SHA, copy only these benchmark fixtures into it from this PR, and serve that worktree on port 4174:

```sh
git worktree add --detach /tmp/eoai-pr3-before 202cfa006d3a98f021e4299c9644e6113bd42f4a
cp tests_browser/data-collection-{backend,helpers,benchmark}.mjs /tmp/eoai-pr3-before/tests_browser/
cp tests_browser/memory-collections-fixture.html /tmp/eoai-pr3-before/tests_browser/
python3 -m http.server 4174 --bind 127.0.0.1 --directory /tmp/eoai-pr3-before
```

Run the same script from this PR's checkout, targeting the baseline server:

```sh
BENCHMARK_BASE_URL=http://127.0.0.1:4174 node tests_browser/data-collection-benchmark.mjs /tmp/data-collections-before.json
```

Recorded environment: Linux, Node 22.16.0, Playwright 1.63.0, Chromium headless shell 153.0.8010.12. CI uses Node 24. Both recorded runs used the same browser, fixtures and completed-operation barriers. The fixture starts with 60 Knowledge sources, 100 persistent memories in five categories, and 12 temporary memories. The standalone panel displays both Memory kinds, so its initial captured card count is 112. Each route starts independently; subsequent operations on a route run after its prior edit/delete, so later starting counts can be 59 or 99.

A `MutationObserver` captures child-list, attribute and text changes under `<main>`. Element counts include the descendants of inserted/removed elements. `mainChildReplacements` counts direct child-list mutations on main, not all list changes; the standalone panel already avoided main replacement but recreated its card containers. `retainedCards` counts the originally captured nodes that remain connected. `routeRenders` counts calls to the management panel's `_render`; mutations can legitimately call it and take keyed reconciliation. The standalone panel has no corresponding `_render` entry point. Counts exclude dialog subtree churn because dialogs sit outside main. Search completion includes the debounced backend result, and the result is followed by two animation frames. No elapsed-time speedup is claimed.

Raw measurements are in `data-collection-performance.before.json` and `data-collection-performance.after.json`.

| Operation | Before added / removed elements | After added / removed elements | Main changes before → after | Retained cards before → after / initial | Route renders before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `knowledgeEdit` | 559 / 559 | 9 / 9 | 1 → 0 | 0 → 59 / 60 | 1 → 1 |
| `knowledgeDelete` | 550 / 559 | 0 / 9 | 1 → 0 | 0 → 59 / 60 | 1 → 1 |
| `knowledgeSearch` | 0 / 0 | 0 / 0 | 0 → 0 | 59 → 59 / 59 | 0 → 0 |
| `knowledgeCachedRefresh` | 0 / 0 | 0 / 0 | 0 → 0 | 59 → 59 / 59 | 2 → 2 |
| `knowledgeChangedRefresh` | 550 / 550 | 18 / 18 | 1 → 0 | 0 → 57 / 59 | 2 → 2 |
| `persistentEdit` | 712 / 712 | 7 / 7 | 1 → 0 | 0 → 99 / 100 | 1 → 1 |
| `persistentDelete` | 705 / 712 | 0 / 7 | 1 → 0 | 0 → 99 / 100 | 1 → 1 |
| `persistentSearch` | 89 / 705 | 0 / 0 | 1 → 0 | 0 → 99 / 99 | 1 → 0 |
| `persistentCategoryMove` | 705 / 705 | 7 / 7 | 1 → 0 | 0 → 98 / 99 | 1 → 1 |
| `temporaryDelete` | 89 / 96 | 0 / 7 | 1 → 0 | 0 → 11 / 12 | 1 → 1 |
| `temporarySearch` | 0 / 0 | 0 / 0 | 0 → 0 | 11 → 11 / 11 | 0 → 0 |
| `standaloneEdit` | 1426 / 1426 | 13 / 13 | 0 → 0 | 0 → 111 / 112 | 0 → 0 |
| `standaloneDelete` | 1413 / 1426 | 6 / 19 | 0 → 0 | 0 → 111 / 112 | 0 → 0 |
| `standaloneSearch` | 143 / 1287 | 0 / 0 | 0 → 0 | 12 → 111 / 111 | 0 → 0 |
| `standaloneCategoryFilter` | 266 / 1293 | 0 / 0 | 0 → 0 | 12 → 111 / 111 | 0 → 0 |
| `standaloneCategoryMove` | 1414 / 1413 | 20 / 19 | 0 → 0 | 0 → 110 / 111 | 0 → 0 |

Knowledge search and an identical cached refresh already had zero structural churn on the baseline; PR3 preserves that behaviour. The changed cached refresh performs one edit, one insertion and one deletion; 57 of the 59 prior cards are retained. In the standalone panel, a delete/category move may also rebuild the small category-chip/datalist strip; unrelated persistent and temporary cards remain connected.

## Validation

- Frontend TypeScript check: passed.
- Frontend unit tests: 31 passed across six files.
- All 61 standalone JavaScript test files: passed.
- Full local Chromium browser suite: 151 passed, 13 backend-dependent tests skipped. This includes 32 new persistent data-collection cases, existing Knowledge/Memory/editor regressions, Functions/Request Rules regression coverage, and explicit cold Overview/Guide source and production-bundle lazy-loading checks.
- Production build: passed; generated distribution assets are included.
- JavaScript syntax checks and `git diff --check`: passed.
- Targeted Python test `tests/test_memory_ui.py`: unavailable locally; test collection stops because Home Assistant is not installed. This Linux environment has Python 3.13.5, whereas the repository's genuine-HA CI uses Python 3.14. This is not a Windows/`fcntl` failure, and tests were not weakened. The static-path registration assertion is updated for the shared helper.

New browser cases cover source and production-bundle CRUD; stable card and input identity; search selection/caret/focus; category moves/counts/filtering/clear; clear all; temporary CRUD/expiry and kind/scope changes; stale Knowledge cache display and changed refresh; editors during background updates; delayed cross-agent/scope/search/pagination responses; backend failure and pending-save authority; and repeated keyboard, card and availability-control actions firing once.

## Explicitly deferred

Global render invalidation/enhancement scheduling (PR4), configuration interaction/guidance hot-path work (PR5), bootstrap redesign, Functions/Request Rules changes, websocket consolidation, UX redesign and virtualization are not part of this PR. PR1 lazy ownership is preserved and guarded by tests.
