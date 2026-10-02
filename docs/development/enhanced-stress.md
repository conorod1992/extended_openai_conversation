# Enhanced nightly acceptance

`.github/workflows/enhanced-stress.yml` runs at 03:15 UTC every night against `develop` and can be launched with **Run workflow**. It is intentionally outside PR collection: Python tests live in `tests_stress/` while `pyproject.toml` collects `tests/`, and the browser file uses a separate Playwright configuration.

The current campaigns are:

| Campaign | New nightly work |
| --- | --- |
| `runtime` | Seeded public Assist conversations across two agents and six users, concurrent batches, unload/setup cycles, registry ownership, stable EOAI manager/service counts after warm setup, and provider ChatLog isolation. Caller-selected conversation IDs are also deliberately reused across users and agents to verify HA ChatLogs cannot cross EOAI privacy boundaries. |
| `lifecycle` | A shared 60-cycle genuine-HA contract across two entries and four subentries checks entity, durable Memory, manager, service and task isolation after every unload/setup. Heavy runs 100 cycles. Active provider turns are also interrupted by real config-entry and conversation-subentry removal; replacement subentries must create a fresh usable runtime. Genuine Management WebSocket replay probes verify revision-protected writes reject stale retries, identical Memory adds deduplicate, and repeated deletes are harmless. A separate child-process campaign installs the delivered component into a clean HA config, configures and uses it through HA, removes it, and repeats the installation. Existing packaged-release and historical release-upgrade workflows provide additional artifact and upgrade evidence. |
| `ai-task` | Two AI Task subentries with distinct models share a parent with a conversation agent. Sequential and concurrent public tasks stay isolated across a model edit, provider failure, reload, and a caller-supplied HA tool round trip. Existing Real HA tests add media attachment and other AI Task paths. |
| `voice-intercom` | Four-user, six-satellite public Assist household requests test mapping changes, unmapped/deleted-user origins, concurrent turns, archive ownership, and reload. Distinct personal Persistent and Temporary Memory markers are checked in every provider payload, including after deleting a mapped HA user and across a Guest transition. Intercom broadcasts span idle/busy satellites, a failing announce service, queued delivery, disappearance, expiry, and reload without duplicate delivery. |
| `archive` | Concurrent retained turns across three private scopes and one shared scope, an unretained origin, searches during writes, a make-private transition, and reload. Every search is checked against the requesting scope. |
| `feature-crossroads` | Seeded Skills publish/replace/remove/rescan operations with an invalid neighbor and locked readers; repeated public local-intent routing across exclusion-policy reloads. Selected ordinary tests additionally exercise streaming speech, usage recovery, live model-catalog refresh, and entity registry/exposure behavior each night. Those selected tests are focused regression evidence, not sustained campaigns for those four families. |
| `setup` | Genuine Home Assistant config flows for provider setup, validation retries, reauthentication, conversation and AI Task subentries, and runtime consumption. Reauthentication is also exercised while an old provider request is in flight to prove the reloaded agent owns a distinct replacement SDK client. |
| `backup` | Fault injection at all seven durable restore category writes, rollback/reload checks, an 83-field maximal config fixture with explicit exceptions and durable-state semantic round trip, a reviewed inventory of persisted agent fields, a public provider-wire probe after restore, and three sanitized fixtures derived from tagged `v4.9.0`, `v5.2.1`, and `v5.3.0` backup schemas and test documents. A live-turn overlap probe verifies restore waits for active agent work and remains authoritative afterward. These test migration and are not byte-for-byte user exports. |
| `backup-transfer` | Genuine WebSocket export and import with a multi-chunk ZIP archive, archive validation, concurrent export/import sessions, out-of-order and duplicate upload rejection, retry, preview token and target-revision checks, stale Apply rejection, expiry, and restore. |
| `request-rules` | Full matcher inventory, text variants, preview agreement, seeded mutations, plus all current matcher × routing scope × API-mode cases through public Assist and SDK wire, and local HA actions. |
| `guest-security` | Cross-product of supported Guest policies for Functions, Knowledge and shared Memory, with security and Function type inventories; public Assist/provider-wire owner–Guest–owner privacy probes, a loaded Group executing an HA service, and live HA permission revocation. |
| `quiet-hours` | Multiple repeated periods across real registry-backed satellites, manual volume and wake-control ownership, media-only controls, an active schedule/policy change with a newly appearing control, transient service failure and retry, exact start/end and midnight boundaries, forward/backward jumps, and Dublin spring/fall DST checkpoints. |
| `functions` | Seeded Function Group loading, disabling and session isolation across 24 tools, 12 groups and 16 conversations; all eleven supported Function types executed through public Assist and serialized SDK wire using local fixtures; representative REST, scrape, SQLite, shell, and file failures returned through the provider wire; delayed due-time HA authorization and configuration mutation checks; and ordinary direct execution/error tests. Provider-wire execution counts are attributed to actual Function types. |
| `provider-resilience` | Reusable seeded provider transport faults under genuine HA Assist and the real OpenAI SDK parser: DNS, connect and read timeouts, TLS, disconnect before headers, 401/403/429/5xx, interrupted assistant and tool streams, malformed tool arguments, unknown response events, contradictory/duplicate stream events, slow provider replies, and disconnect after an HA service action. Assertions cover visible failure, private archive and usage accounting, no duplicate side effects, and next-turn recovery. Recorder/history unavailable, erroring and delayed cases are exercised as tool-scoped failures. |
| `memory-knowledge` | Hundreds of private Memory records and Knowledge sources with reload comparison, bulk Temporary Memory expiry, concurrent multi-user private Memory requests inspected on SDK wire before and after reload, a Guest Mode privacy probe, and on-demand Knowledge search results after disabled/deleted source mutations. |
| `large-installation` | Ten agents, 100 rules, 60 configured tools, 20 groups, 500 memories and 150 Knowledge sources in normal mode; setup, backup, reload, public Assist probes and measured stage timings. Three additional combined tiers drive one agent through up to 240 Function Tools, 48 groups, 900 exposed HA entities with selected attributes, 360 persistent and 95 temporary memories, 240 Knowledge sources, 160 archived turns, a 60,000-character prompt, and 16 live conversation-history turns at heavy intensity. The SDK wire is inspected for valid schemas, retained safety/current-user context, bounded attributes and requests, dropped old history, privacy isolation and recovery from a provider context-limit rejection. Existing Memory/Knowledge, archive and provider-wire campaigns also run with this manual/nightly selection. |
| `persistence` | Future opaque agent fields through known edits, backup and reload; a current export refused by the actual latest published older payload; unsupported backup versions rejected without mutation; linked Function/Group and Rule/Group write failures and reloads; the existing seven-store backup fault matrix and process-crash tests; graceful HA shutdown at both Temporary and Persistent Memory Store commit boundaries; repeated Temporary Memory expiry across forward/backward clock jumps and restart. |
| `chaos` | Weighted seeded Memory, Temporary Memory, Knowledge, Request Rule, Guest schedule, config and entity-exposure mutations; backup checkpoints/restores, reloads and public request probes after every step. |
| `browser` | One Chromium panel through 80 seeded route changes with recurring Memory, Knowledge and Rule create/edit/delete, browser Back/Forward and refresh, checked against fixture backend state; 20 disconnect/reconnect cycles mix Knowledge writes with bounded forced reads; gated late reads cover eight management surfaces; narrow/wide layout, keyboard dialog and large-text checks; two tabs against one genuine HA backend reject stale Request Rule and post-backup-restore Assistant saves. |
| `browser-engines` | Curated Firefox and WebKit nightly paths cover major management sections, lazy imports, Memory CRUD, rejected-save retry, stale reads, reconnection, repeated panel mount/unmount, offline recovery, bfcache restoration when granted by the engine, narrow/large-text focus, and a bounded seeded route/mutation journey. A keyboard-only Memory journey uses Tab, Shift+Tab, Enter, Space, Escape and arrow keys across mobile navigation and dialogs. Each engine also runs same-object two-tab conflicts through genuine HA WebSocket schemas; fixture-only browser results are labeled separately. |

| `process-chaos` | Nightly and manual child-process crash/restart acceptance for immediate Function side effects and durable delayed Functions. |

The browser campaign also stages the latest published frontend payload and runs old-shell/new-backend, stale lazy-chunk and 404/refresh journeys against genuine HA management WebSocket clients. A separate real HA shell test uses HA's own auth tokens and browser WebSocket to delay and drop configuration saves and revoke a session while an edit is open. Browser-local malformed cache, slow and dropped reads, owner-to-restricted permission changes, failed-save drafts and twelve sequential saves are checked through the existing bridge. The bridge faithfully forwards management messages to authenticated HA WebSocket clients; only the real shell case claims genuine browser-to-HA WebSocket transport.

Scheduled nightly runs every campaign at both `normal` and `heavy` intensity in separate matrix jobs, including process crash paths. `heavy` multiplies stress-specific Python operation counts by four and browser transitions by four. Manual dispatch selects one intensity. The workflow generates one shared seed. Every campaign now uploads a small `evidence-*` artifact, including its `eoai-enhanced-evidence/v1` certification envelope and chronological traces. The envelope records the exact tested SHA, campaign, intensity, seed, HA/Python versions and, where applicable, HA matrix point and released payload version. Failed Python tests still write their trace through fixture teardown, with test node, outcome, last operation and a short failure reason. The consolidated certification job downloads these envelopes and reports the actual pass/fail result for every expected matrix row; a missing row cannot be called green. Use the campaign, intensity and seed shown there to reproduce a failure. Browser Playwright traces and screenshots are retained in separate failure artifacts.

The manual `diagnostics` campaign deliberately runs four failing Python probes (generic, a public Assist/provider DNS fault, a genuine HA config-entry lifecycle, and a resource assertion) and a failing fixture-backed Playwright probe. Their wrapper steps expect the failures and verify the actual uploaded and downloaded trace, log, screenshot and Playwright ZIP contents before reporting success. Fake credential, private Memory, private Knowledge and sensitive prompt canaries are injected into the Python probe; the verifier scans its generated JSON and sanitized log set for leaks. `ci/enhanced_evidence.py` redacts sensitive trace fields and credential-shaped values, sanitizes captured test output before printing/upload, and bounds diagnostic log size. The Playwright probe is explicitly fixture-backed; it verifies browser artifact plumbing, not genuine HA browser behavior.

The lifecycle campaign also runs the same genuine-HA contract in a separate three-point matrix: the oldest HA version declared in `hacs.json`, current stable, and the live Core `dev` commit. This matrix is scheduled/manual only. Each job records its exact installed HA version and uploads its resource baseline/checkpoints/peak and seed trace on both success and failure.

`.github/workflows/upgrade-acceptance.yml` independently runs the latest published release and reviewed historical migration epochs (`6.8.2`, `6.5.0`, `6.3.1`, `6.2.0`) against current `develop`. Each epoch installs its actual tagged integration in a genuine Home Assistant process, creates state through its config flow, replaces only the integration, verifies migration and public Assist, saves and restarts current configuration, then mutates and restores a current-version backup. The custom Function Tool Management WebSocket scenario runs for all epochs except `6.2.0`: its old save handler deep-copies a Home Assistant object and fails under current HA before creating a tool. The 6.2.0 baseline and browser upgrade tests remain mandatory; that specialised historical tool scenario needs a compatible older HA runtime or a separately reviewed state-generation path. Add a historical tag to the explicit matrix when a new migration boundary warrants it. `6.2.0` is the oldest tagged Responses integration with a matching tag and manifest version; several earlier tags in this fork contain a `4.8.3` manifest under a newer tag name.

For a local Linux environment with the repository's test requirements installed:

```sh
STRESS_SEED=123 STRESS_INTENSITY=normal pytest tests_stress/test_request_rules_matrix.py -v -s --asyncio-mode=auto --timeout=600
STRESS_SEED=123 npx playwright test --config=playwright.stress.config.mjs
```

Home Assistant's Python test harness requires Linux. The Windows Python installation cannot run the Real HA tests because Home Assistant imports `fcntl`.

When adding a persisted agent setting, update `BACKED_UP_AGENT_FIELDS` in `tests_stress/test_backup_inventory.py` after reviewing the export and restore behavior. When adding a Request Rule matcher, action or routing scope, update the classified inventory and generated cases in `tests_stress/test_request_rules_matrix.py`. Keep a new stress test under `tests_stress/` or name a browser test `*.stress.mjs` to preserve the separation from normal CI.

The canonical frontend route acceptance classification lives in `tests_stress/frontend_route_inventory.json`. A new route in `frontend-navigation.js` fails `test_frontend_route_inventory.py` until its level is reviewed. `route-inventory.stress.mjs` opens every route via the shipped navigation at desktop, tablet, and mobile widths, waits for settled content, checks browser and backend errors, refreshes, and navigates away and back. Routes with `read-write` or `full-crud` levels also need representative editing tests in the browser and Real HA suites; the level is a minimum contract, not a claim that the route sweep performs full CRUD by itself.

`tests_stress/agent_field_contract.json` classifies each persisted agent field's save/reload, HA persistence, backup, and migration semantics. The existing backup field set still gates actual exported keys. `management_action_inventory.json` records every literal section/action dispatched by the Management WebSocket, with evidence references; `public_service_inventory.json` does the same for registered EOAI services. Their Python tests compare against production declarations so a new field, action, route, or service fails nightly until classified. The negative dummy-surface checks run without changing production files.

The Step Summary labels evidence layers. **Model-level** means a manager or state machine was exercised directly. **Real-HA** includes genuine setup, stores, registries or public Assist calls. **Provider-wire** checks SDK-serialized requests and the scripted local provider response. **Browser** checks the mounted management UI; **process-boundary** runs a restarted child process. Counts of model transitions are never described as provider requests or tool executions. `tests_stress/health.py` offers configurable durable and public invariants for long stateful journeys.

`tests_stress/evidence_manifest.json` is the reviewed feature-to-evidence map. The runtime campaign checks it against supported Function types, Request Rule actions, backed-up subsystems, and referenced test files. Render its Markdown table with `python ci/enhanced_manifest.py`. Blank cells are not claimed evidence; each feature needs only the layers that make sense for its behavior.

The suite is complementary to the bounded PR tests and deliberately reuses selected Real-HA acceptance where that adds cross-feature nightly evidence. A failing operation trace identifies the seed and last completed operation; use the same seed to reproduce, then inspect the first violated invariant and Playwright trace or HA log. The suite is broad evidence, not proof of all supported histories. Remaining deliberate gaps are increasingly combinatorial, including a complete provider-wire error matrix for every Function type, every possible browser same-object conflict surface, and deterministic process kills around every durable store write.


The `voice-intercom` campaign also runs `test_native_audio_delivery.py`. A
software integration registers native HA STT, TTS and Assist satellite entities.
A deterministic PCM WAV recording passes through the native satellite pipeline,
real EOAI/SDK conversation and TTS HTTP output. The fixture checks transcription,
user/device/satellite identity, spoken reply, decoded WAV frames and playback
acknowledgement. STT cancellation and output disconnect/reconnect each precede a
successful next interaction. This covers software delivery, not speech recognition
quality or physical speaker acoustics; normal CI needs no microphone or satellite.

The `runtime` campaign boots the unchanged delivered integration in an independent
HA process for `test_process_resource_soak.py`. Fragmented local provider streams,
concurrent Assist turns, durable Memory adds/readbacks/deletes, background usage
accounting, two config-entry reloads, a broken request and cancellation recovery
exercise real runtime lifetimes. After warm-up it records four resource windows:
RSS, descriptors/handles, thread and inspectable executor queue counts, config/storage
bytes and files, event-loop lag, and Assist/management p95 latency. Normal windows
span 15 seconds each; heavy spans 60 seconds each, plus a 10-second warm-up and
process startup/shutdown. JSON metrics and the seed survive in existing stress
artifacts on failure. Broad baseline-growth ceilings and repeated material growth
catch runaway resources; generous 5-second loop-lag and 15/20-second management/Assist
ceilings avoid hosted-runner microbenchmark gating. Negative tests reject artificial
leaks/stalls while accepting a warmed plateau and modest timing variation.

`test_provider_real_connection_pool.py` extends the existing socket acceptance with
an ephemeral local TLS CA and a real reverse proxy that buffers a slow, fragmented
SSE upstream. Certificate rejection must produce a handled EOAI failure before the
same verified client recovers after trusting the CA; idle closure requires a fresh
connection. A TLS/TCP break during the continuation after a committed HA tool action
must reject replay, recover on a subsequent conversation and allow a new intentional
action. Both Chat Completions and Responses use the actual SDK. Only the deliberate
rejected handshake on the fixture server is accounted for; unrelated asynchronous
errors remain failures. No provider internet requests or live OpenAI calls are used.

The mandatory-case catalog includes these journeys and resource negative proofs.
Certification additionally requires five process windows, two audio deliveries,
four trusted proxy requests and two socket/tool recovery cases. Existing Voice
Identity/Quiet Hours/intercom semantics, logical leak counters and synthetic fault
matrices remain complementary coverage.

The `voice-intercom` campaign additionally interleaves two native software
satellites with distinct users, recordings, transcripts and provider replies.
One TTS output fails while the other remains usable; fetched WAV data and native
playback acknowledgements prove recipient association and subsequent recovery.
Intercom separately retains the real idle-stability timer, flaps idle before that
timer completes, observes actual TTL callbacks, holds a native playback
acknowledgement past expiry, then verifies a subsequent successful delivery.

The Chromium `browser` campaign warms native YAML and populated condition editors,
user/entity pickers, Function discovery and import dialogs in one HA document.
Eight equivalent post-GC windows use the existing heap/node/document/listener
plateau checks; a final Ctrl+S must commit exactly once. Two genuine assistants
also receive distinct committed writes, a held save completion with the selector
locked, a second-tab revision conflict, fresh authoritative readbacks and a held
Function-discovery response after close/switch. Firefox and WebKit run only the
relevant Composite recovery and two-assistant journeys. Chromium alone also
abandons an admitted real upload, verifies quota remains occupied until elapsed
TTL and supported lazy cleanup, and reconnects for a successful preview/cancel.

The same Chromium campaign runs the existing axe sweep: its original 62 whole
route/editor scans plus 20 scoped invalid-YAML, save-error, restore-confirmation
and destructive confirmation/error scans across light/dark and desktop/mobile.
Only unchanged exact findings can reuse the reviewed baseline; new targets,
serious/critical findings or increased counts fail. Removed findings must also
be removed from the baseline. Native populated-picker interaction is exercised
functionally, but semantic acceptance for that state is deliberately deferred:
the current HA picker exposes `aria-required-parent` on its combo-box item and
an unnamed clear button (`button-name`). Resolving these critical findings would
require changes to HA's native picker internals. They have not been baselined.
The existing frontend diagnostics workflow's optional `diagnostic_picker` input
includes all four populated-picker theme/viewport scans and retains fail-closed
review, so it reports these findings instead of certifying them.

The manual-only `long-lifetime` campaign extends the existing booted resource
soak to at least 30 elapsed minutes, with at least 25 minutes of measured idle,
periodic Assist/Memory traffic, nine resource windows, four actual entry reloads,
an expired default-900-second upload reclaimed by the next transfer operation,
and a final healthy Assist request. The actual archive retention callback runs
on a documented 60-second harness interval rather than its production daily
interval; this proves callback lifetime/cleanup, not a full day's elapsed
retention. At least 20 callbacks must actually fire. Resource growth and latency
use the existing warmed plateau/trend bounds. Select `long-lifetime` with normal
intensity in Enhanced's manual dispatch. It is excluded from `all`, the nightly
schedule, PR and release gating; a separate weekly scheduler would add workflow
complexity without improving the elapsed-time assertion. It uses the same exact
candidate, operation minimums and execution certificate as other campaigns.


Native HA compatibility also uses the same mandatory-case catalog and structural
execution ledgers. Real-HA public journeys retain their four-case contract on stable
and dev; minimum HA additionally selects representative native tools/targets,
permissions, Local Handling, voice identity, Script actions/waits, registry changes,
Knowledge/Memory wire behaviour, Quiet Hours and Intercom cases. These selectors
live once in `nightly_execution_contract.json`; missing, skipped or xfailed cases
fail the compatibility lane even when pytest exits successfully.

`ha-browser-compatibility.yml` retains Chromium on minimum/stable/dev and adds only
stable Firefox, WebKit and 390px WebKit. All profiles exercise the genuine HA shell,
native user/entity picker selection, YAML validation/save and an actual Assist
outcome. Mobile WebKit additionally downloads a real backup and uploads it for
preview without applying a destructive restore. Each lane validates both pytest
and Playwright execution evidence and uploads version/profile-specific artifacts.

The 390px WebKit lane uses a narrow genuine HA shell. It does not certify an iOS virtual keyboard or physical Companion device: iPhone emulation cannot reliably replace CodeMirror content through Playwright keyboard input. The lane retains native YAML validation/save and backup download/upload preview assertions.

The added minimum-HA feature selection, Firefox/WebKit/narrow native-shell lanes, semantic picker extension and execution gates run only on scheduled or manual overnight invocations. PR/push selections remain the existing four public version cases and the three Chromium native-shell version lanes. New governance and SDK cache regression cases live in existing overnight stress files.

Overnight stable compatibility resolves the final Home Assistant release from PyPI once per workflow and installs a matching pytest fixture version. Evidence must report that exact HA release; a newer fixture pulling a beta cannot satisfy the stable lane. Existing PR environment selection is retained.


`frontend-latency-diagnostics.yml` runs nightly at 02:37 UTC and manually, never on PR/push. It measures the immutable invocation candidate against a fixed reviewed source baseline in `ci/frontend_latency/overnight_policy.json`, using the same current harness and dependency environment for both. Raw cold-route readiness/LCP/assets and backend samples, source/harness SHAs, key environment versions/fingerprint and comparisons are retained for 30 days. Updating the pinned baseline requires reviewing the data and dependency declarations; CI never adopts a slower candidate automatically.

Performance remains reporting-only while at least a week of runner variance is gathered. Reports flag route readiness only when it increases by both more than 1000 ms and 50%, or backend operations by both more than 100 ms and 100%. These are review signals, not release gates. Functional route readiness, integration-asset failures, missing samples/routes/operations, source mismatches and environment mismatches fail immediately. Review several nightly reports before introducing any hard timing ceiling.

The same overnight job uses pinned axe-core 4.11.0 inside the genuine HA shell. It scans all 22 major routes in light/dark desktop themes, five critical routes at 390px in both themes, and Function Tool/Request Rule editor states: 62 required scans. Open shadow roots and native editor content are included; HA shell content outside the EOAI panel is outside this scan’s scope. Nested native/Lit descendants must settle and the YAML editor must initialize before scanning. Empty or missing scans fail. New serious/critical WCAG A/AA findings and growth in an existing finding’s node count fail; minor/moderate findings and incomplete/manual-review results remain visible in artifacts. This complements keyboard/layout testing and does not establish complete WCAG compliance.

`accessibility_baseline.json` records the initial audit’s 278 serious and two critical nodes across 98 exact selectors, with exact state/theme/viewport counts, source SHA, reason and review expiry (2026-11-01). Settled scans reproduce 272 contrast nodes, four unnamed native YAML textbox nodes, two narrow native-scroller focus findings, and two collapsed native-picker ARIA-parent findings. Contrast needs a focused palette review; native component findings need focused accessibility remediation. This testing change does not silently modify product colours or HA-owned components. No axe rule is disabled and no wildcard selector is allowed. Expired or obsolete allowances reject evidence. Baselines may only be updated by reviewed repository changes; the workflow never writes them. Artifacts retain rule IDs, selectors and failure descriptions without page HTML, authentication tokens or private HA data.

## Overnight concurrency and resource boundaries

The functions campaign now gates parallel-safe native tools through public Assist and both SDK protocols: safe calls must overlap, mixed side-effectful calls must remain serial, and provider outputs retain call order. Local REST/Scrape servers exercise oversized declared and chunked bodies, trickled timeouts, truncated bodies and invalid encoding, followed by a healthy call through the same tool.

Composite acceptance rejects cyclic, excessively deep and excessively wide trees before side effects, including singleton sequences accepted by HA validation. Nested cancellation must stop later script actions and leave the next Assist request healthy. This exposed unbounded recursive validation and adds explicit limits of 32 levels and 256 functions. Remote decoding and unavailable scrape results now become recoverable HA errors.

The large-installation campaign runs six conversations owned by three users through concurrent deferred summaries, out-of-order completion, follow-ups, failure and cancellation. It scales the existing pending limit to four for a deterministic capacity/fallback test; it does not claim to stress 128 live provider connections. Provider requests must contain only the owning conversation's private markers and preserve recent turns.

Process chaos schedules 50 normal / 100 heavy delayed calls through the real scheduling path, stops HA, makes persisted calls overdue, and boots again. Disabled tools and removed users must be discarded; valid actions execute exactly once, persisted state and waiters drain, loop lag remains below a generous five-second ceiling, and public Assist and management remain healthy. A third boot proves completed calls do not replay. This exercises clean process restart and existing commit boundaries, not arbitrary crash timing.

Chromium endurance adds equivalent-state post-GC heap, DOM, document and listener samples after route/module warm-up. Eight normal / sixteen heavy windows retain numeric diagnostics and reject sustained growth exceeding both generous absolute and relative thresholds. Firefox/WebKit keep the existing endurance path. The test exposed repeated help keyboard bindings retaining detached dialogs, unbounded native-selector readiness waiters, and Chromium radio-group ownership retaining removed import dialogs. Help now resolves the current dialog through one root handler; selector readiness uses one weak waiter per panel; removed dialogs release radio-group ownership before detachment. The existing editor matrix also holds a model-catalog response while reasoning is selected; the response now preserves the current choice instead of restoring its stale snapshot.

These cases and minimum-operation evidence are selected only by enhanced overnight campaigns. Ordinary PR test selections and jobs are unchanged. All providers are deterministic local fixtures; no live OpenAI traffic or physical hardware is required.

The existing browser and Firefox/WebKit native selections also run
`test_native_editor_ownership_and_satellite_registry_recovery`. Its six native
cases hold Save validation after the submitted YAML reaches backend validation,
or hold UI completion after a real commit. Cancel/reopen must preserve the new
draft, controls and destination; group, Request Rule and Guest references are
checked before and after a fresh-manager reload. HA config-entry and manager
Stores use the real atomic writer, with independent disk readback. The registry
journey replaces Kitchen's entity/device association in the mounted document,
saves through native entity/user pickers, and checks private markers in genuine
public Assist provider requests from both devices. A first-fetch outage must
retry in the same mounted panel. These cases remain Enhanced-only; the small
editor-operation and registry-selection regressions extend the existing ordinary
Node suite because they deterministically protect ownership, lookup settlement
and retry without HA, browser or network costs.


AI Task attachment continuity is mandatory in `ai-task`: both SDK API modes
must dispatch the caller tool, retain the originating image/instructions and
associated result on the continuation, and exclude that history from a later
independent task. The existing `feature-crossroads` catalogue journey saves a
real `ai_task_data` consumer, rejects a narrowing registered reset without
changing disk state, then resets/reloads with a compatible choice. Evidence
requires two image continuations/isolation checks, one rejected/successful AI
Task reset and three actual SDK requests. These expensive cases stay in the
existing Enhanced selections. The direct AI Task guard invariant joins the
already-running catalogue unit suite because it is small and deterministic;
it also proves that AI Tasks do not load conversation Request Rules.


Durable publication acceptance uses existing selections and genuine Store IO.
Archive, Knowledge and Memory use the real atomic-writer fixture; Quiet Hours
uses its native non-atomic prepared-data writer boundary. `archive` and `persistence` require both first-intent
replacement boundaries, independent journal readback, unrelated publication and
fresh-manager convergence. `memory-knowledge` and `persistence` require held
management mutations, entry into real public Assist reads, an actual target
atomic failure, SDK tool-result inspection and committed retries for both stores.
Readers may wait for settlement. `quiet-hours` requires observation/ownership
write failures for both volume and wake sound with live/reloaded recovery and
original-value restoration (eight cases). Semantic recovery counts come from
manager/device assertions, rather than the generic Store fault matrix. All cases
are mandatory at the candidate SHA; existing minima and reviewed allowances
remain unchanged. No expensive acceptance case is added to ordinary PR CI.
