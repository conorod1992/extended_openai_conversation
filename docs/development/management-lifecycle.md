# Management lifecycle migration

Baseline: live origin/develop `890d18629794f177cb3d61ac54578b43a60b56a7` (2026-09-18).

## Before: installer inventory

The numbered modules are evaluated sequentially by management-bootstrap. Other installers arrive through static dependencies, lazy editor dependencies or feature entry points. "Delegates/conditional" calls a captured implementation on at least one path; replacement/addition does not. Dynamic instrumentation wrappers in bootstrap also wrap `_navigate`, `_loadSection`, and `_render`.

| Bootstrap order | Module | Assigned methods and delegation |
| --- | --- | --- |
| 1 | management-state-safety.js | `_setConfigDirty` (delegates/conditional); `_syncConfigDirty` (delegates/conditional); `_syncConfigControlDirty` (replacement/addition); `_navigate` (delegates/conditional); `_handleRouteChange` (delegates/conditional); `_startFreshGuestPolicy` (delegates/conditional); `_setupGuestSelectors` (delegates/conditional); `_loadAgents` (delegates/conditional); `_loadSection` (delegates/conditional); `_invalidateAfterMutation` (delegates/conditional); `_render` (delegates/conditional); `disconnectedCallback` (replacement/addition) |
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
| 18 | management-provider-credentials.js | `_render` (delegates/conditional); `disconnectedCallback` (replacement/addition) |
| 19 | management-configuration-clarity.js | `_render` (delegates/conditional); `disconnectedCallback` (replacement/addition) |
| 20 | management-configuration-guidance.js | `_call` (delegates/conditional); `_render` (delegates/conditional) |
| 21 | management-decision-guidance.js | `_render` (delegates/conditional); `_confirm` (delegates/conditional) |
| 22 | management-conversation-default-label.js | `_render` (delegates/conditional) |
| 23 | management-settings-polish.js | `_render` (delegates/conditional) |
| 24 | management-overview-health-clarity.js | `_loadAgents` (replacement/addition); `_render` (delegates/conditional); `_call` (delegates/conditional); `_render` (delegates/conditional) |
| dependency/feature | agent-config-loader.js | `_render` (delegates/conditional) |
| dependency/feature | guest-mode-ui.js | `_formatDate` (replacement/addition); `_loadSection` (delegates/conditional); `_memories` (delegates/conditional); `_conversations` (delegates/conditional); `_guestPolicyView` (delegates/conditional); `_updateVisibleList` (delegates/conditional); `_searchArchive` (replacement/addition); `_openSession` (replacement/addition); `_bindActions` (delegates/conditional) |
| dependency/feature | management-bootstrap.js | `connectedCallback` (delegates/conditional); `_render` (replacement/addition) |
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
