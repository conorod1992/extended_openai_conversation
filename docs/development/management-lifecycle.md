# Management lifecycle migration

Baseline: live origin/develop `890d18629794f177cb3d61ac54578b43a60b56a7` (2026-09-18).

## Before: installer inventory

The numbered modules are evaluated sequentially by management-bootstrap. Other installers arrive through static dependencies, lazy editor dependencies or feature entry points. "Delegates/conditional" calls a captured implementation on at least one path; replacement/addition does not. Dynamic instrumentation wrappers in bootstrap also wrap `_navigate`, `_loadSection`, and `_render`.

| Bootstrap order | Module | Assigned methods and delegation |
| --- | --- | --- |
| 1 | management-state-safety.js | `_setConfigDirty` (delegates/conditional); `_syncConfigDirty` (delegates/conditional); `_syncConfigControlDirty` (replacement/addition); `_navigate` (delegates/conditional); `_handleRouteChange` (delegates/conditional); `_startFreshGuestPolicy` (delegates/conditional); `_setupGuestSelectors` (delegates/conditional); `_loadAgents` (delegates/conditional); `_loadSection` (delegates/conditional); `_invalidateAfterMutation` (delegates/conditional); `_render` (delegates/conditional); `disconnectedCallback` (delegates/conditional) |
| 2 | management-action-safety.js | `_saveGuestPolicy` (delegates/conditional); `_call` (delegates/conditional); `_render` (delegates/conditional) |
| 3 | management-function-dependencies.js | `_call` (delegates/conditional); `_invalidateAfterMutation` (delegates/conditional) |
| 4 | management-feature-status.js | `_memories` (delegates/conditional); `_knowledge` (delegates/conditional); `_diagnostics` (replacement/addition); `_testAgent` (replacement/addition); `_styles` (delegates/conditional) |
| 5 | management-memory-settings.js | `_isDraftView` (delegates/conditional); `_canAccessView` (delegates/conditional); `_content` (delegates/conditional); `_bindActions` (delegates/conditional) |
| 6 | management-capabilities-ia.js | `_isDraftView` (delegates/conditional); `_configSectionsForView` (delegates/conditional); `_content` (delegates/conditional); `_knowledge` (delegates/conditional); `_dialogs` (delegates/conditional); `_knowledgeValues` (delegates/conditional); `_setKnowledgeEditorDisabled` (delegates/conditional); `_openKnowledge` (delegates/conditional); `_bindActions` (delegates/conditional) |
| 7 | management-voice-identity.js | `_content` (delegates/conditional); `_bindActions` (delegates/conditional) |
| 8 | management-permission-boundaries.js | `_canAccessView` (delegates/conditional); `_call` (delegates/conditional) |
| 9 | management-rendering-performance.js | `_render` (delegates/conditional) |
| 10 | management-loading-performance.js | `_call` (delegates/conditional); `_canAccessView` (delegates/conditional); `_prepareScopeCatalogVisit` (replacement/addition); `_loadScopes` (delegates/conditional); `_invalidateAfterMutation` (delegates/conditional); `_loadSection` (delegates/conditional); `_render` (delegates/conditional) |
| 11 | management-function-repair.js | `_call` (delegates/conditional); `_content` (delegates/conditional); `_render` (delegates/conditional) |
| 12 | management-route-performance.js | `_loadSection` (delegates/conditional); `_render` (delegates/conditional) |
| 13 | management-navigation-search.js | `_render` (delegates/conditional); `_clearConfigDraft` (delegates/conditional) |
| 14 | management-toolbar-layout.js | `_render` (delegates/conditional) |
| 15 | management-history-pagination.js | `_loadSection` (delegates/conditional); `_searchArchive` (replacement/addition); `_openSession` (replacement/addition); `_render` (delegates/conditional) |
| 16 | usage-input-footprint.js | `_loadSection` (delegates/conditional); `_usage` (delegates/conditional); `_bindActions` (delegates/conditional) |
| 17 | debug-management.js | `_loadAgents` (delegates/conditional); `_getRun` (replacement/addition); `_viewRun` (replacement/addition); `_copyRun` (replacement/addition); `_render` (delegates/conditional); `_canAccessView` (delegates/conditional); `_loadSection` (delegates/conditional); `_content` (delegates/conditional); `_render` (delegates/conditional) |
| 18 | management-provider-credentials.js | `_render` (delegates/conditional); `disconnectedCallback` (delegates/conditional) |
| 19 | management-configuration-clarity.js | `_render` (delegates/conditional); `disconnectedCallback` (delegates/conditional) |
| 20 | management-configuration-guidance.js | `_call` (delegates/conditional); `_render` (delegates/conditional) |
| 21 | management-decision-guidance.js | `_render` (delegates/conditional); `_confirm` (delegates/conditional) |
| 22 | management-conversation-default-label.js | `_render` (delegates/conditional) |
| 23 | management-settings-polish.js | `_render` (delegates/conditional) |
| 24 | management-overview-health-clarity.js | `_loadAgents` (replacement/addition); `_render` (delegates/conditional); `_call` (delegates/conditional); `_render` (delegates/conditional) |
| dependency/feature | agent-config-loader.js | `_render` (delegates/conditional) |
| dependency/feature | guest-mode-ui.js | `_formatDate` (replacement/addition); `_loadSection` (delegates/conditional); `_memories` (delegates/conditional); `_conversations` (delegates/conditional); `_guestPolicyView` (delegates/conditional); `_updateVisibleList` (delegates/conditional); `_searchArchive` (replacement/addition); `_openSession` (replacement/addition); `_bindActions` (delegates/conditional) |
| dependency/feature | management-bootstrap.js | `connectedCallback` (delegates/conditional); `_render` (delegates through busy-main helper) |
| dependency/feature | management-temporary-memory.js | `_loadSection` (delegates/conditional); `_scopePicker` (delegates/conditional); `_memories` (delegates/conditional); `_dialogs` (delegates/conditional); `_openTemporaryMemory` (replacement/addition); `_temporaryMemoryDirty` (replacement/addition); `_closeTemporaryMemory` (replacement/addition); `_saveTemporaryMemory` (replacement/addition); `_deleteTemporaryMemory` (replacement/addition); `_bindActions` (delegates/conditional) |
| dependency/feature | quiet-hours-ui.js | `_loadSection` (delegates/conditional); `_content` (delegates/conditional); `_bindActions` (delegates/conditional); `_styles` (delegates/conditional) |
| dependency/feature | usage-chart.js | `_call` (delegates/conditional); `_usage` (replacement/addition); `_dialogs` (delegates/conditional); `_bindActions` (delegates/conditional) |

## Dependencies and overlaps

- State safety owns dirty navigation, cache TTL and capture-phase actions. Action safety and function dependencies wrap calls/invalidation after it.
- Memory settings, capabilities IA and voice identity extend route classification, access, content and actions before rendering optimisation. Permission boundaries restrict those routes.
- Rendering optimisation wraps the already decorated renderer, but its persistent path bypasses earlier render wrappers. Loading adds capture-phase save/correctness handlers on top of it.
- Loading replaces overview, scope visit policy, and wraps scope TTL, request deduplication, access and rendering. Route performance adds a second asset/loading race guard and request-rule search; both start data and assets concurrently.
- Function repair follows loading so its call interception participates in the final request chain. Navigation search/toolbar require the persistent hosts. Configuration clarity/guidance/decision guidance depend on those final controls and labels; settings polish follows badge producers.
- History pagination, usage footprint, debug and quiet hours wrap route loading for feature-specific data. These cannot simply be loaded concurrently without separating their hooks.
- Overview health clarity replaces `_loadAgents` (including the earlier state-safety wrapper), speculatively prefetches the remembered overview, and repairs the loading patch's lossy overview projection with another `_call`/`_render` pair.
- Bootstrap intercepts registry definition to install all hooks before element upgrade; property replay and timing wrap the resulting class. Removing this bridge before all remaining installers migrate would introduce lifecycle races.

## Stages decided before implementation

1. Native route asset/data lifecycle and request helper; fold scope TTL, summary overview and save correctness into direct ownership. Preserve lazy assets, save response reuse and rule-save deduplication.
2. Native persistent renderer with independent region ownership; preserve dialog nodes and avoid unchanged region replacements without multiplying existing action listeners.
3. Remove obsolete sequential installers, consolidate initial overview loading, and parallelise independent config/history work. Retain scope selection dependency unless a valid selected scope is established.
4. Add regression/browser coverage, compare the same fixture baseline, push incremental milestones and open a PR against develop. Assess broader stale caching against mutation and dirty-state correctness; do not blindly cache memories/history.

Performance-critical: bootstrap, loading, routing, rendering, state-safety cache, initial overview and lazy loaders. Remaining modules predominantly supply feature behaviour, access boundaries, or presentation decorators and are migration follow-ups.

## Baseline validation

All existing standalone tests/*.test.mjs passed. Browser measurements use the shipped browser fixture (mock backend, local HTTP), not a live HA deployment; they establish structural costs rather than production latency. Windows Python initially served .mjs as text/plain; the local test server uses explicit JavaScript MIME mappings.

## Implemented ownership

- The host owns `_loadSection` -> `loadRoute` -> `_loadSectionData`. A single route controller starts asset/data work together and guards stale asset completion. No performance installer wraps `_loadSection` or `_call`.
- The native request path owns timestamp normalization, rule-save deduplication and mutation invalidation. `management-actions.js` binds the existing capture-phase save/import/rule correctness actions explicitly; saves still reuse the response without reloading agents/configuration.
- `management-cache.js` owns route TTL reads/writes. Knowledge and Request Rules remain isolated per agent. Knowledge can display an expired list while revalidating; an unchanged refresh retains its object and DOM. Rules keep their bounded fresh-cache policy. Scope catalogues are shared between memory/history routes per agent, with a non-sliding 30-second TTL. Memory/history mutations and full restore invalidate the catalogue; stale in-flight generations cannot repopulate it.
- `management-renderer.js` owns the persistent shell, main host and independently updated regions. Equal route markup is not replaced or rebound. `management-dialogs.js` owns the five persistent core dialogs (Knowledge, Memory, session, reassignment, confirmation) with delegated actions; feature editor dialogs still use their existing replace/bind lifecycle.
- Initial agent loading and remembered-overview prefetch now live in the native route lifecycle. The health decorator no longer replaces `_loadAgents` or intercepts `_call` to recover fields discarded by another patch; the overview summary is preserved intact.
- Voice and memory-settings UI implementations are lazy route assets. Three sequential performance installers were removed (24 -> 21 entries). The customElements bridge remains for the other feature installers; those still require deterministic installation before upgrade.

## Request dependencies and cache boundaries

Configuration is independent of the scope catalogue and begins concurrently on conversations. History requests still wait for validated scope selection: `_applyScopes` may change the selected scope, so blindly requesting history in parallel would risk loading the wrong owner's records. A recent catalogue from memories removes this dependency entirely on the next conversations visit. No compound endpoint was needed.

The agents list remains authoritative for selection. Existing remembered-agent overview prefetch removes its dependency when stored IDs remain valid. A first-ever visit still needs agents before the selected-agent summary; inventing a new aggregation endpoint for this case was not justified.

Broader caching was assessed but deliberately does not include live memory/history, Guest Mode, configuration drafts or diagnostics. Their state can change outside management or is correctness-sensitive. The read-heavy Knowledge list now supports stale-while-revalidate; existing fresh Request Rules/Knowledge caching is retained. Usage/overview remain candidates for a separate freshness policy accounting for live conversation activity.

## Measurements

Reproduce with `node tests_browser/management-benchmark.mjs BASE_URL OUTPUT.json` after installing the pinned Playwright runner. The raw five-sample runs are in `measurements/management-before.json` and `measurements/management-after.json`. Before is untouched `890d186`; after is this refactor. Both use Chromium, the same shipped mock backend, an empty browser context, local HTTP and an injected 40 ms per backend request. Module counts use executed JavaScript coverage, not preload requests. Byte counts are decoded initial frontend resources. These are controlled structural comparisons, **not production HA latency measurements**; no meaningful cold-load win is claimed.

| Scenario (median of 5) | Before | After |
| --- | ---: | ---: |
| Cold navigation until first usable overview | 199 ms | 200 ms |
| First configuration-heavy section | 51 ms | 51 ms |
| Memories | 102 ms | 95 ms |
| Conversations after memories | 137 ms | 52 ms |
| First Knowledge visit | 49 ms | 51 ms |
| Overview revisit | 49 ms | 52 ms |
| Fresh cached Knowledge revisit | 2 ms | 2 ms |
| Expired Knowledge: first usable list | 50 ms | 2 ms |
| Initial evaluated frontend modules | 46 | 46 |
| Initial decoded frontend bytes | 627,164 | 596,777 |
| Sequential bootstrap installers | 24 | 21 |
| Conversations management requests after memories | 5 | 4 |
| Conversations serial request stages after memories | 3 | 1 |
| Core dialog-host replacements per route navigation | 1 | 0 |
| Main/dialog replacements for two unchanged renders | 2 / 2 | 0 / 0 |

The total evaluated module count is unchanged: two direct ownership modules offset two deferred feature implementations. Initial bytes fall about 4.8%; the material gains are request concurrency and avoiding DOM destruction, rather than a dramatic cold-start/module-count improvement. Ordinary destination navigation still replaces main content once, preserving the outer shell/main host. In an immediate-backend exploratory run, the first configuration route also dropped from three main replacements to two by suppressing duplicate asset-completion rendering. The fixed-latency comparison needs only one main replacement in both versions.

## Validation and remaining work

- All standalone JavaScript checks pass; frontend type check, 30 Vitest tests and build pass.
- Full local Chromium suite: 48 passed, 13 environment-gated genuine-HA/upgrade tests skipped. Six new browser regressions cover node identity, single dialog submission, cache TTL/invalidation, independent requests, stale completion, feature lazy loading and stale Knowledge refresh.
- Backend waterfall/residual/asset registration subset: 16 passed (one unrelated configuration normalization test excluded). A broader initial subset gave 23 passed / 6 failed; all six reproduce on untouched develop with this machine's HA template validation error (`Validates schema outside the event loop`). HA's default pytest plugin requires Unix `fcntl`; local backend tests use explicit pytest-asyncio loading and a workspace temporary directory. CI is the Linux/genuine-HA validation path.
- A browser run had one pre-panel timeout; its trace showed no mounted element, and the isolated test and subsequent complete suite passed. A benchmark run similarly timed out before overview; the complete raw comparison contains successful reruns only. A subsequent 20-context cold-load stress run passed without failures. Treat cold timings as noisy, not evidence of a cold-start improvement.
- Remaining feature render decorators still scan the DOM and mutate it after rendering. Feature editor dialogs still rebuild when main content changes. Converting those owners to lifecycle hooks/components is the next high-value step.
- State-safety retains dirty-navigation/Guest baseline hooks; history pagination, temporary memory, quiet hours, usage footprint and debug retain feature loading wrappers. Bootstrap instrumentation still wraps navigation/loading/render, and registry interception remains until all pre-definition installers can be explicitly composed.
- Larger route bodies still render strings. A future route component migration should retain controls within changed pages, rather than caching entire private history DOM or adding more wrapper layers.

### CI follow-up

Linux CI exposed an import-registration gap: native modules were still listed in the optional loading optimizer's asset extension. Their registration (including direct loader dependencies) now lives in the base `MANAGEMENT_FRONTEND_MODULES` registry. The actual `test_management_frontend_routes_cover_module_imports` regression plus focused backend tests pass locally (15 tests).

The initial Linux stable job had 4,107 passing tests and only that registration failure. HA-dev also reports existing tool-result API compatibility failures (`ToolResultContent(..., tool_result=...)` against the changed HA API). Comparison against develop run 35305758839 at unchanged base `890d186` found the registration test was the only newly failing test name. This task does not change conversation/provider compatibility code. Genuine-HA Chromium, browser smoke, packaged-install smoke, lint, type checks and frontend build passed before the registration-only correction.
