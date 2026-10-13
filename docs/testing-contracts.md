# Testing shared guarantees

Feature regressions remain useful, but this layer compares production entry points against shared guarantees. A new writer or action route should be added to the relevant matrix rather than receiving only a happy-path test.

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
