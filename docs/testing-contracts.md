# Testing shared guarantees

Feature regressions remain useful, but this layer compares production entry points against shared guarantees. A new writer or action route should be added to the relevant matrix rather than receiving only a happy-path test.

Recovery must include a usable repair operation, not only preservation and a diagnostic. Restored malformed Group identities require revision-bound collection repair; duplicate identity references require an explicit choice. Repairs validate the complete replacement and preserve unrelated raw Functions and configuration. Retained inactive Request Rules must not block unrelated pack imports, while incoming rules remain strictly validated and disabled by default.

Migration candidates must be assessed against their original values. YAML aliases may share schemas between stock, custom and quarantined definitions: a migration detaches the provider schema it owns before modifying it, so unrelated definitions and backend payloads retain their values. Repeated startup must be idempotent after persisted reload.

Editor authoring state, validated configuration and validation errors have separate ownership. Invalid Voice assignment rows must survive same-assistant navigation and remain correctable; an error must never replace the mapping object. Save admission must reject invalid retained rows even outside the Voice route, and Discard must retire their authoring state.

Voice recovery must invalidate hydrated controls when the retained row projection changes even if its valid mapping object stays unchanged. Native picker display initialization must not clear the selected user's semantic value while the user catalogue loads; explicit selection and clear events remain authoritative before and after hydration.

An acknowledged repair may leave later edits in its editor. Both complete Function and Group repair support revision-bound follow-up saves after the first write clears quarantine. Bulk Function repair preserves an available selected Skill loader; ambiguous unusable source can still be repaired to an explicitly disabled loader without enabling it.

Archive turn order survives a backwards wall-clock correction across a monthly storage boundary. Optional per-session sequences preserve that order through cold reload, backup replacement and restore-journal recovery. Legacy reads retain their existing order without a storage rewrite; an append upgrades every affected month in the same journal. Invalid, duplicate or mixed sequences retain the original storage evidence and block destructive writes. Sequence gaps remain valid and are not renumbered by pagination or replacement.

A validated full-agent journal authorizes recovery of rejected native category stores; corrupt data alone does not. Target and rollback tests preserve quarantine evidence, reload through ordinary managers, and retain the journal across failed writes and retries. Archive recovery inventories only exact owned monthly files. Usage recovery reconciles previously rejected handles through native reads, while healthy legacy totals mirrors retain their existing best-effort behavior.

Combined recovery cases quarantine every native category together, interrupt the final bootstrap write, then retry with warm or cold manager state. They compare every restored category and the saved retention policy against the selected journal generation, including a subsequent Usage reload under that policy.

Read results carry meaning independently of whether they contain records. Initial catalogue pending, failed and successful-empty states must render distinctly, with a deliberate catalogue retry and no retry storm from Home Assistant updates. Relative Usage periods use Home Assistant's local calendar rather than the latest activity or a retained summary date. Diagnostics share canonical configuration representation parsing and isolate failures between sections.

Mobile dialog action reachability is checked geometrically after ordinary scrolling. Programmatic focus, locator clicks and scroll-into-view behavior alone do not establish that a user can reach a clipped footer.

## Entry-point matrix

| Guarantee | Routes | Evidence |
| --- | --- | --- |
| Invalid requests cannot publish | Configuration save/update, import preview/current/new | Genuine HA: unchanged data, IDs, revision, client and loaded agent |
| Full request validation before persistence | AI Task fast/advanced native flows | Genuine HA: invalid combinations remain forms, no subentry mutation |
| Narrow settings contract | Settings update | General model/search configuration is rejected |
| Saved requests survive catalogue activation | Apply/reset, Conversation/AI Task | Failed publication preserves active and pending catalogues; valid controls publish |
| Provider changes preserve child compatibility | Conversation/AI Task, hosted search on/off | Authentication alone does not permit incompatible child configuration |
| Live/reload decisions agree | Configuration save/update | Token changes preserve runtime; model changes rebuild device metadata |
| Failure remains machine-readable | Assist/process × provider/rule/local intent | Error code, failed Usage, and no continued listening |
| Caller CONTROL permission precedes effects | Native/Script/nested Script | Real restricted HA user: denied state unchanged, allowed context retained |
| Broadcast permission precedes queueing | Service/WebSocket/model/local | Real HA policy; only discovery/delivery replaced, denied queue untouched |

Native Conversation creation accepts only a name; detailed configuration goes through Management. Settings deliberately accepts a restricted subset. Full disaster-recovery restore is not setup import and has its own preservation/recovery contract.

The fast catalogue/provider matrix runs in ordinary unit CI. Genuine HA matrices are discovered by Real HA Acceptance on PRs and nightly runs. Existing oldest/stable/dev public journeys continue to check version compatibility. The matrices replace external provider transport or delivery, never the authorization or request validator they aim to verify.

## Stateful and concurrency layer

`tests/test_stateful_contract_sequences.py` runs fixed seeds on every PR. Its independent memory oracle checks content, provenance, stable identity, owner isolation and ranked pagination after each operation and after cold-manager reload. Failures include the seed and complete operation trace.

`tests_real_ha/test_state_transition_contracts.py` repeats a short sequence with actual HA atomic files. Its Quiet Hours journey combines a save outage, unavailable controls, successful disable, manager teardown/recreation and returning devices; it checks both restoration and preservation of an independent device change.

Six deterministic maintenance cases cross Usage/Temporary Memory with restore/delete/cancelled restore. Events expose exact gate admission and exclusive attempts; the tests retain actual manager and guarded Store code and replace only native disk I/O. They assert bounded completion, committed pruning, released ownership, deletion tombstones and sibling progress. No sleeps are used to manufacture the interleaving.

Delayed execution combines missing-agent retry, durable record reconstruction and definition edits. Request Rule repair tests distinguish failed writes from committed disable/change/delete operations.

The nightly `memory-knowledge` campaign adds 300 operations (1,200 at heavy intensity) with the existing recorded stress seed and evidence ledger. The new case is part of the mandatory execution contract, so dropping its workflow selector fails certification. Existing selective restore, metadata refresh, same-title deletion/recreation, in-flight removal and child-process crash/restart tests remain in the genuine HA lifecycle suites; this layer supplements rather than duplicates those expensive scenarios.

## Provider boundary and test sensitivity

`tests/test_runtime_readiness.py` asserts that an accepted save reaches the actual
initial provider request. It covers provider/API combinations, the Azure Chat
tool boundary, Conditional finalization, Group loading, generated and provider-owned
tools, YAML schema values, quarantined legacy definitions, availability mutations,
imports, duplication and restoration. A genuine HA Script/service probe preserves
typed YAML values in backend payloads while provider-facing schemas require JSON.

`request.validate_runtime_readiness` is the pure mutation-boundary contract layered
on existing configuration preparation. It reuses request capability resolution,
Function Group assembly, integration tool generation, finalizer insertion and the
runtime tool-count guard. It checks the guaranteed initial request with no loaded
Groups or request-specific discovery, installed Skills, retained-data scopes or
Knowledge catalogue. Those dynamic capabilities remain subject to runtime checks.
Repair-owned definitions use the same usable projection as runtime; their raw
payloads remain available for repair. New restores validate before journal creation;
replaying an existing recovery journal retains its original recovery contract.

`tests_real_ha/test_provider_boundary_contracts.py` keeps HA dispatch and the real OpenAI SDK. A strict HTTP fixture checks serialized Chat Completions and Responses histories against an independent call/result oracle. It rejects incomplete, duplicate and orphan exchanges and unsupported Function schemas, and requires attachment bytes in every historical request. Violations are recorded separately so an integration error handler cannot hide a failed protocol assertion.

The journeys combine corrected pre-dispatch arguments with a one-execution budget, an executed action followed by a lost continuation and equivalent replay, text-only production plus diagnostic requests, and historical image/PDF follow-ups. The attachment journey uses Core's AI Task entity interface with a retained ChatSession: the public AI Task action creates a new session and does not itself expose a conversation ID parameter. Text-only capability expectations come from the fixture, not EOAI's catalogue.

The nightly mutation workflow adds `contract-sensitivity`: four reviewed mutations omit authorization, skip request validation, misreport a failed result, and discard an unavailable control's restoration baseline. Each selected genuine HA test must pass unmodified and fail by assertion after mutation. Collection, import, fixture, timeout and skipped outcomes cannot count as kills. The runner uses an isolated archive of the exact Git commit, restores the shadow source after each case, and publishes SHA, baseline/mutation JUnit and logs. A survivor or stale/ambiguous mutation anchor fails the campaign. This is a small guarantee check; it does not claim exhaustive mutation coverage.


## Remediation invariants and transitions

The PR suite and Enhanced nightly campaigns share the existing Function behavior
case generator and real-HA execution helper. Seven reviewed PR witnesses combine
Request Rules, Composite Functions, native script actions, disabled Bash failures,
result capture (including whole-data templates and native result types), Guest
Mode, explicit unretained identity, Memory and active conversation transitions.
The expected side effects and personal-data visibility are defined independently
of production validators. Native `continue_on_error` is exercised without reading
a nonexistent result from a failed service. The nightly Request Rules campaign
expands these witnesses to 24 cases (96 at heavy intensity), using its recorded seed
and privacy-safe operation evidence.

The shared provider wire oracle validates actual SDK payloads from AI Task tests
in both API modes: strict closed objects and required properties, bounded names,
mode-specific fields and the Azure Chat tool budget. Deliberately invalid payloads
prove rejection; OpenAI and Azure Responses are checked without the Chat-specific
limit. A separate advertisement oracle exercises every advertised statistics
period and proves it detects a deliberately removed runtime option.

Browser witnesses and the seeded frontend state machine share a configuration
transition helper. It compares rendered inputs, drafts and saved configuration
across navigation, immediate Knowledge saves, a failed full save, retry and reload.
Existing reset and exposed-attribute regressions cover page-specific resets and
authoritative empty selections. Browser failures attach the seed and fixture state.

Real HA permission tests compare native, script, nested script and Request Rule
control effects using actual restricted users; local intent tests separately
exercise genuine READ permissions. The parity test exposed a Request Rule bypass:
native rules now use the same Script-local service authorization adapter as script
tools, without imposing model-tool exposure rules on administrator-authored rules.

Idle lifecycle tests advance HA time without another request at 1-, 7- and 30-day
Usage retention settings and exercise disabled archive collection through reload,
expiry and restart recovery. The feature-crossroads nightly lane includes them.
Actual Assist pipeline tests compare progressive speech and final transcripts for
conditional answers with zero/text preambles, already streamed answers and one/two
tool rounds. Existing partial-stream and multi-turn endurance coverage remains in
the same nightly lane. No additional test framework or live billable provider
acceptance is required by these deterministic checks.

## Execution, result ownership and durable cleanup

`test_execution_result_lifecycle_contracts.py` in both test trees exercises these guarantees together:

- Replay identity describes the prepared operation. Native-only Composite trees compare every child; a child that observes the original representation or consumes another child's response prevents unsafe whole-operation equivalence. JSON Schema `default` annotations remain annotations. HA-owned tools apply their authoritative HA schema defaults.
- Rejected, completed and uncertain execution are distinct from result presentation and provider acknowledgement. A completed effect survives unexpected exceptions, cancellation and output processing failures. Partial or uncertain execution blocks changed-argument retries of that tool in the same conversation until a subsequent successful turn resolves the ambiguity. First-turn failures retain the conversation identity too.
- A Request Rule alias belongs to its producing invocation. Both native script variables and final response construction read that invocation's published result. Administrators can retain shared `response_variable` names; skipped or failed producers do not publish their previous contents. Only EOAI `call_function` actions receive generated result machinery. Nested producing aliases remain unsupported.
- Local Function consumers retain typed values and exact literal text. Request Rule aliases retain the separate 16,384-byte bound. Model output uses a 32,000-character presentation bound: structured overflow is a complete explicit result, while text truncation is marked. Literal JSON-looking text is never inferred to be an integration envelope.
- Memory and Knowledge searches continue through ranked matches using `next_offset` as `offset` with the same query, category/source filters, retrieval mode and unchanged corpus/ranking inputs (including Hybrid embedding availability). Restart at offset zero after mutations or permission changes. Whole-record budgeting includes escaping and metadata; list paging keeps its existing budgeting. An individually oversized record raises an explicit error rather than returning a nonadvancing page.
- Native lazy template fields retain HA evaluation timing. Concrete condition/trigger selectors resolve supported request captures before validation; unsupported dynamic placements fail at save. This shared validator is used by management, import, sharing and repair paths. Invalid retained rules remain available with repair diagnostics.
- Native implementation validation belongs to its backend and runs recursively before any Composite child executes. Stored configuration stays serializable; runtime hydration remains a separate boundary.
- Knowledge and Temporary Memory decode canonical records on import, cold load and failed-save reconciliation. Accepted normalized values are the values indexed, published and re-exported. Startup normalization preserves unrelated malformed Knowledge records and restore-journal evidence.
- Retired transfer files are immediately unusable but remain owned and quota-accounted until physically deleted. Cleanup attempts every detached file and retries individual failures with bounded backoff. Shutdown always attempts owner-wide cleanup; its ownership handle remains retryable after failure. Pre-registration allocation failures remain owned too. A committed restore followed by upload deletion failure returns `cleanup_pending: true` with its successful restore result; this does not require repeating the restore.


## PR regressions and Enhanced certification scope

Prepared-write fault injection selects the native writer through
`tests/ha_store_test_helpers.py`. The adapter binds to the target Store instance:
legacy Core delegates through `_write_prepared_data`, while newer Core owns the
writer in its I/O backend. Unknown layouts fail explicitly. Physical-file
calibration covers both serialization modes, atomic and non-atomic writes,
failure before writing, lost acknowledgement after writing, a held writer,
unaffected sibling storage, fresh readers and recovery after removing a fault.
Quiet Hours stress scenarios retain their original failure-phase, service-call,
ownership and restart assertions; compatibility changes do not replace native
serialization or writing with an in-memory store.
Read-fault tests invalidate the native cache through the same shared helper,
without assuming that Store owns the cache object. Stress tests reuse the native
suite's conditional `hass_storage` fixture and canonical `real_store_io` opt-in,
so Core's early Stores and later recovery readers share physical I/O from their
construction. Tests without that opt-in retain the normal in-memory fixture.

Browser fault injection must distinguish a server-confirmed rejected mutation
from a lost or malformed acknowledgement. Only the rejected case permits a
direct retry; uncertain policy saves require an accepted read first, preserving
edits authored after submission. Destructive retention saves include the actual
confirmation step and prove that no write precedes it. Shell bootstrap seeds
onboarding through HA's Store API so mocked and native I/O receive the same
state. Native picker journeys use the shared option/legacy-button adapter and
continue asserting the resulting values and real backend effects. Browser bridge
clients may authenticate lazily on first use, but never reconnect or replay a
command after a later disconnect.

Regular frontend CI also collects the complete Enhanced static suite, its
targeted recheck and curated Firefox/WebKit suites using the existing execution
reporter. It launches no browser,
keeps collect-only ledgers in an isolated temporary directory, and compares exact
execution counts against the partition inventory and mandatory case catalog.
Adding a test without reviewing both catalogs fails this short preflight before
the longer browser campaigns. The final certificate still requires actual
passing executions from both static and native partitions.

The Function representation corpus in `tests/fixtures/function_configuration_representations.json`
is shared by dependency, migration, serialized restore and journal-recovery checks.
It distinguishes legacy raw empty values from explicitly empty serialized
collections, preserves typed backend values and disabled definitions, and retains
quarantined siblings unchanged. Repeated startup must be idempotent, keep healthy
siblings usable, and never silently enable an unavailable capability. Unsupported
data does not authorize replacement; unrelated programming, I/O and cancellation
failures still propagate. The native process-crash journey also checks quarantine
and healthy-sibling operation after recovery and another clean restart.

Reviewed Setup sharing also round-trips safe quarantined Function records and
malformed Group collections through serialized import. Group membership projection
removes references only to omitted Functions; existing unknown references remain
repair-owned. Credential omissions must not activate an ambiguous Tool collection
or promote members out of disabled/on-demand groups. Unit boundary cases and the
native backup-transfer export collector cover these contracts without mutating
the source configuration.

Reviewed exports reconcile selected Skills after Tool and Group omissions, then
round-trip through the ordinary configuration importer. Typed YAML and unsafe Group
metadata must not leave a dangling loader dependency or enable a disabled loader.
Nested Composite result aliases belong to the parent step; hydration, persistence
and execution retain those aliases without passing them to strict child validators.
Provider connection recovery is shared by the credential endpoint and registered
`change_config` action, including authentication-failed, unchanged and disabled
entries. Provider-aware configuration reads, writes, copies, sharing and browser
catalogue lookups use the actual parent provider. Cached scalar snapshots also
track provider context while reusing unchanged Function validation. Portable
configuration validation preserves explicit provider-specific efforts until the
destination provider is known; request assembly still owns API compatibility.

Mutation wire tests serialize actual backend results and feed them to production
JavaScript acknowledgement consumers, including Function, Group and Rule deletion.
Rejection requires evidence that no write
committed; missing/malformed responses and transport loss remain uncertain.
Confirmed commitment retires only its submitted draft before presentation and
refresh, including committed responses carrying warnings or partial outcomes.
An uncertain configuration import consumes its preview but retains source text;
another preview requires an authoritative assistant-list refresh. These checks
extend existing owner and acknowledgement helpers rather than adding a global
transaction coordinator.

Short-term Memory editor checks hold a committed update's reply while more
content, category and expiry changes are authored. The acknowledgement advances
only the submitted baseline; later input remains available for a second Save or
explicit Discard. Source and shipped-bundle journeys also cover an unchanged
editor closing normally, a replaced editor retaining its own draft, and expiry
conversion in a Home Assistant timezone distinct from UTC. Held Voice user
catalogues are tested across discard and panel recovery for both successful and
failed replies, including a newer explicit user clear.

Retained data-editor retry checks require a rejected write to leave backend
records unchanged, then verify a corrected save retires its old error alert
while preserving input authored during acknowledgement. A later independent
rejection must still publish a new error, and the final retry must save exactly
the retained values. The same source and bundle journey covers both Memory
editors and Knowledge.
Admission guards also prove that required-field client validation preserves the
current rejection without dispatch, and that unresolved Long-term Memory and
Knowledge writes retain their error and block another transport call even if
the Save button is forcibly re-enabled in the test.

Broadcast retry tests drop the actual queue acknowledgement, retry its immutable
operation identity, and assert a single admission. They cover payload/caller
binding, bounded capacity without eviction of live receipts, expiry and server
replacement, authorization changes, cancellation, and failures after insertion.
Uncertain attempts survive snapshot refresh; edits and revoked authentication
lifetimes require explicit new-send confirmation. Fresh sends obtain a current
server retry window without renewing an uncertain predecessor; failed preparation
does not count as a dispatched send. Real-browser disclosure/rebind
journeys preserve Rule Sharing files, review and export choices, and initialization
failure remains retryable. Already-open mounted disclosures initialize without
needing another toggle. These journeys are registered in the nightly browser
execution inventory in addition to PR smoke collection.

PR lint runs the nightly selection and execution-catalog guards without Home
Assistant fixtures. A new real-HA file must be selected by Enhanced or explicitly
assigned to an existing genuine-HA acceptance job in
`tests_stress/nightly_real_ha_exclusions.json`. New selected test functions also
need review in `tests_stress/nightly_execution_contract.json`; do not replace the
catalog with only the cases from a targeted collection.

Backend PR checks exercise the existing Composite business-outcome and resource
boundary stress cases. Frontend browser checks exercise delayed-read navigation
once per PR, alongside the normal sharded smoke suite. SDK range changes run the
required pinned SDK wire matrix, including its tested upper boundary; newer SDKs
remain advisory until validated and deliberately admitted.

Enhanced summaries and `certification-final.json` report their campaign and
intensity scope. `passed` means the selected scope passed. Only a successful run
selecting all campaigns at both normal and heavy intensities sets
`full_programme_passed`; a campaign-only green run cannot certify the full
programme. Scheduled long-lifetime heavy execution remains in its dedicated lane.

Management authentication regressions cover a retained panel receiving a different
HA user, including mutation of the same `hass.user` object and A/B/A transitions.
The mounted editor remains owned by its first known account: its content and
dialogs leave the document while another account is active, with a reload action
to open a fresh panel for that account. Returning to the original account preserves
its authored nodes and drafts, but cannot revive retired reads or live watches.
Nested editors and confirmations also regain their native modal ordering and
focus after the original account returns; restoring an `open` attribute alone
does not restore the browser's top layer.
Live status resumes with fresh authentication ownership and the original
remaining polling budget; paused replies cannot publish after resumption.
Broadcast reads carry their authentication generation from request creation,
including when their feature module binds only after the account round trip.

Native history, statistics and Energy regressions check current Assist/HA READ
visibility at the read boundary, including nested Composite children and an
earlier child or Energy manager load that yields before exposure is revoked.
Current visibility narrows the originally admitted Guest/caller scope; it never
widens it. The browser resilience slow-read witness waits for the actual delayed
Memory response and usable collection, since the route heading may paint earlier.


Release-day CI resolves fixtures for the exact released Home Assistant version. If a published custom-component test plugin is unavailable, only reviewed immutable source pairs in `ci/resolve_ha_test_plugin.py` may supply the bridge. Home Assistant 2026.11.0 ships the official fixture package in Core: the bridge relocates its import namespace for the existing tests, pins the exact Core dependency and verifies wheel version, actual released version constants and source provenance. It registers one fixture plugin. Older releases retain their pinned upstream generator; no beta fixtures are relabelled and no older runtime substitutes for the selected release. The unused standalone respx pytest plugin is disabled as recommended by the official package, allowing Home Assistant to install its HTTP compatibility alias first.


HA 2026.11 live-history regressions admit the actual Guest input through Core's
ChatSession/ChatLog contexts, fail preparation with both exceptions and
cancellation, then resume the owner and inspect the provider wire. Untouched
inputs that never acquire continuity ownership must leave the source log. Owned
failed turns remain available for recovery; nested invocations cannot claim or
remove an ancestor's input. Both provider APIs run these controls, alongside
listener restoration and inherited-task lifetime tests. Catalogue transition
checks exercise explicit reasoning inherited by model-only rules, including
prior conversation routes, disabled/request-scoped controls and explicit route
overrides, without mutating saved choices or publishing blocked metadata.


Native delegation checks also admit a parent input on a log not owned by EOAI,
then delegate identical text and ID through HA's conversation entry. Preparation
and claimed-turn errors/cancellation must preserve the parent's history. Unowned
logs retained this way are excluded from subsequent EOAI claims by exact live-log
identity, with weak references following Core's lifetime. First Guest admissions
remain isolated on owner resumption; initial HA-default claims and owned failed
turns retain their IDs and recovery history. Replacement-log and collection
controls prevent exclusion from surviving the history it protects.


Sequential parent-delegation checks first complete a successful child request,
then fail the next delegation. Success must never make an unowned parent eligible
for EOAI admission cleanup. Parent IDs bind to separate conversations by assistant,
privacy scope and Guest state before namespace resolution. Actual HA counter
effects verify that lost continuations and cancellation keep replay protection
through that binding, while successful acknowledgement permits an intentional
repeat. Native Core cleanup, a replacement parent with a still-live child, explicit
reset and retired weak callbacks must not resurrect old context or erase new
bindings. Initial Assist callers receive the owned conversation ID; retries through
the original live parent ID retain the same failed-turn context.

Claim cleanup registration is bounded per native session and owner, including
long-lived parent-bound conversations. Cleanup from a retired session or registry
cannot release the replacement claim.

Active requests through a live parent ID refresh its native inactivity clock
without rebinding native authentication. A separate-user call cannot keep another
user's native parent session alive. Native cleanup is exercised past the initial
five-minute boundary while the same binding and active model history survive.

Delayed tasks inherit native ContextVars. Tests close and optionally expire the
parent before releasing the delayed delegate, then inspect actual provider input
and Core's current log/session registries. Closed contexts stay retired in the inherited task
on success, error and cancellation, while active callers keep their native contexts; a live parent binding is found by its stored
identity, and retired history must never reappear in a new request.
