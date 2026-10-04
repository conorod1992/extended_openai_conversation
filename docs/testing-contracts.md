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
