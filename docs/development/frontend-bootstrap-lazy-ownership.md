# Frontend Performance PR1: bootstrap / lazy ownership

Base: `develop` at `35524d02ea390b8f4d3fff865d07226e64f69d34`.

The management entry now asks the existing `management-route.js` feature registry
for configuration guidance, Guest rendering, persistent/temporary Memory helpers,
Request Rules UI, capability controls, and feature status/diagnostics. Overview's
onboarding and health decorators belong to its existing implementation import.
No module receives or patches the panel constructor.

`routeFeatureKeys()` describes shared feature dependencies of each route. All of
them use the existing `featureModules` and `featurePromises` maps. Ready modules
are reused, concurrent imports are coalesced, and a rejected import clears its
pending promise. Native browser module-fetch failures can require a page reload;
the browser tests cover the visible section error and recovery on a fresh load.

The panel renders a loading placeholder until the route's feature and editor
assets are ready. `loadSectionAlongsideAsset()` still starts data and assets in
parallel, and its existing route/asset token rejects stale completion. Only the
pre-existing `DATA_FEATURES` routes wait for their data helpers. Memory's small
scope/state preparation stays synchronous so its backend request is not delayed
by the newly lazy editor. Module publication does not mutate panel state.

The existing persistent dialogs and page-draft coordinator remain core. In
particular, the small Knowledge availability-control template must be present
when its persistent dialog is first created. Search focus remains pending while
assets load, and subsequent model inspection honors that destination. Production
preloads use Vite's relative base because Home Assistant serves the bundle below
the integration URL, not `/assets/`.

## Direct-import audit

The baseline has 28 unique directly imported files (194,745 source bytes before
transitive imports). Repeated import declarations for routing and runtime guidance
are counted once. A = shared infrastructure, B = route feature, C = configuration
editor, D = helper moved to an existing feature owner. Mixed files are explicitly
split rather than making shared navigation depend on asynchronous imports.

| Baseline direct import | Class | Ownership after PR1 |
| --- | --- | --- |
| `management-decision-guidance.js` | A / B / C | Shared confirmation scope/styles extracted; rule summaries, live testing and editor badges load with their feature |
| `agent-config-loader.js` | A | Small existing loader/copy adapter; editor implementation remains dynamic |
| `management-navigation-search.js` | A | Shared search, including setting vocabulary without editor DOM enhancements |
| `management-toolbar-layout.js` | A | Persistent toolbar |
| `management-configuration-clarity.js` | A / C | Compatibility exports; pure metadata and shared agent/dirty navigation have explicit core owners |
| `management-configuration-guidance.js` | C / D | Lazy configuration feature; tiny synchronous websocket projection extracted |
| `management-settings-polish.js` | A | Shared Guide/settings layout styles and adapter |
| `management-overview-health-clarity.js` | D | Existing lazy Overview implementation |
| `management-memory-settings.js` | C | Configuration feature, including Memory settings/model reset binding |
| `management-capabilities-ia.js` | B / C | Home Assistant, Web Skills and Knowledge routes; persistent dialog template stays core |
| `management-feature-status.js` | B | Memory, Knowledge and Diagnostics; diagnostic CSS accompanies its renderer |
| `management-temporary-memory.js` | B | Memory feature; small owner-scope preparation stays core |
| `management-page-drafts.js` | A | Shared save/discard/navigation coordinator, including drafts left on other routes |
| `management-cache.js` | A | Cache expiry and state safety |
| `management-dialogs.js` | A | Persistent dialog lifetime and dispatch |
| `management-renderer.js` | A | Existing shell/region composition |
| `management-actions.js` | A | Shared mutation, save and correctness guards |
| `management-route.js` (two declarations) | A | Single feature registry, loading, backend concurrency and stale-load tokens |
| `frontend-navigation.js` | A | Route metadata, navigation and search index |
| `guest-mode-ui.js` | B / D | Guest/Memory/history family; timestamp and browser-state preparation extracted as small shared helpers |
| `guide-page.js` | A | Existing lightweight lazy facade |
| `overview-page.js` | A / D | Lightweight lazy facade; onboarding/health moved into its implementation |
| `usage-format.js` | A | Small number formatting used throughout the shell and routes |
| `request-rules-ui.js` | B | Request Rules route, including dialogs and binding helpers |
| `management-action-safety.js` | A | Shared mutation/agent-switch safety |
| `management-function-dependencies.js` | A | Small cache dependency constants |
| `management-permission-boundaries.js` | A | Shared route authorization and non-admin projections |
| `management-state-safety.js` | A | Shared unsaved navigation, dialog protection and lifecycle cleanup |

`agent-config-editor*` and backup/restore were already behind configuration
imports. PR1 removes configuration guidance and binding dependencies from cold
Overview/Guide and verifies those existing editor boundaries in both source and
production builds. This PR does not claim backup/editor internals are split by
every individual configuration subsection.

## Validation and scope

Local final results: frontend TypeScript check passed; 31 Vitest tests in six
files passed; all 60 `tests/*.test.mjs` files passed; all 68 shipped source modules
passed `node --check`; the production build and a second-build tracked-artifact
comparison passed; `git diff --check` passed. The repository has no separate JS
lint/formatter command. The complete Chromium run passed 103 tests with the 13
existing environment-gated HA/release tests skipped by their normal conditions.

Browser assertions cover cold Overview and Guide in source and production,
first/repeated feature visits, absence of unrelated requests and JS coverage,
parallel backend work during delayed imports, stale navigation, import failure,
agent changes and reconnects, and cold search followed by back/forward. Existing
CRUD, dirty-state, dialog, editor, render-invalidation and lifecycle journeys are
retained. Source-location assertions follow the new owner while preserving their
behavioral assertions. The mock harness now mirrors Home Assistant's `route`
property updates on `popstate` and can load the checked-in production manifest.

Render-invalidation/hot-path scheduling, targeted guidance updates, persistent
keyed collections, broad event delegation, backend websocket consolidation and
UX redesign are intentionally deferred to later performance PRs.

Python/HA validation was attempted locally but collection stops in
`homeassistant.runner` with `ModuleNotFoundError: No module named 'fcntl'` on
Windows. Genuine HA stable/upcoming acceptance remains a Linux CI gate; no CI
test or requirement was disabled.

## Measurement method

Five fresh Chromium pages per mode, shipped mock backend, 40 ms injected backend
latency, no real HA credentials. The original benchmark was run before edits;
its raw baseline is retained. The final comparison runs the same updated
benchmark against a detached baseline checkout and the candidate. Its only
measurement change waits for the visible Overview dashboard as well as completed
data, avoiding a sample taken before the baseline's lazy renderer arrived. The
baseline checkout only received the harness's optional production-entry support.

Decoded bytes come from browser Resource Timing and evaluated files from JS
coverage. In source mode those files are modules; in production they are bundled
chunks, so the two counts must not be compared across modes. Timings are local
mock-harness measurements, not claims about real HA or WAN latency. The default
Windows server needed explicit JavaScript MIME types for `.mjs`; both checkouts
used the same server configuration and a larger connection backlog.

Reproduce (serve each checkout at its own URL with JS/MJS MIME types):

```sh
node tests_browser/management-benchmark.mjs http://127.0.0.1:4173 result.json
node tests_browser/management-benchmark.mjs http://127.0.0.1:4173 result-bundle.json --bundle
```

## Results

| Cold Overview metric | Source before | Source after | Production before | Production after |
| --- | ---: | ---: | ---: | ---: |
| Decoded frontend JS bytes | 384,158 | 305,369 | 268,613 | 218,567 |
| Evaluated JS files | 36 | 31 | 12 | 15 |
| Cold usable median (ms) | 250 | 225 | 237 | 249 |
| Cold usable min–max (ms) | 239–304 | 212–270 | 220–287 | 228–283 |

Decoded JS falls **20.5% in source** and **18.6% in production**. Source evaluation falls from 36 to 31 modules (13.9%). Production chunk count increases from 12 to 15 because shared helpers are split by Vite; it is not a reduction in production file count. The 12 ms production median increase is within these overlapping five-sample ranges; this PR claims less decoded/evaluated source work, not a demonstrated production latency improvement.

| Navigation median (ms) | Source before | Source after | Production before | Production after |
| --- | ---: | ---: | ---: | ---: |
| assistant/basics | 50 | 50 | 50 | 56 |
| data-memory/memories | 101 | 101 | 102 | 97 |
| data-memory/conversations | 52 | 51 | 52 | 55 |
| data-memory/knowledge | 45 | 51 | 50 | 50 |
| overview | 50 | 47 | 45 | 49 |
| data-memory/knowledge (cached repeat) | 2 | 2 | 2 | 2 |

Later lazy navigation remains usable in this mock harness: initial configuration and conversation/Knowledge routes take roughly one injected backend round trip; Memory takes roughly two (scope plus list). Cached Knowledge returns in a few milliseconds.

DOM writes on cold Overview remain one shell write and one main-region write in all final samples. Two unchanged renders produce zero tracked writes in every mode. No render architecture change was used to obtain the byte reduction.

Raw five-run samples (including route writes, backend calls, expired-cache timings and errors):

- [baseline-source](benchmarks/bootstrap-pr1-baseline-source.json)
- [after-source](benchmarks/bootstrap-pr1-after-source.json)
- [baseline-bundle](benchmarks/bootstrap-pr1-baseline-bundle.json)
- [after-bundle](benchmarks/bootstrap-pr1-after-bundle.json)
- [Original pre-edit baseline](benchmarks/bootstrap-pr1-initial-baseline.json)

The original pre-edit source baseline also measured 384,158 bytes and 36 modules (194 ms median). The paired final rerun is used for timing comparison because wall-clock conditions changed during development.
