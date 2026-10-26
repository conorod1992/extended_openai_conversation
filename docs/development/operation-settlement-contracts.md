# Cancellation and operation settlement

Batch 4 classifies ownership by its actual outcome contract. A shield alone
keeps a child alive; it does not retain the caller's lock or lease until that
child finishes. None of the settlement helpers decides whether an external
effect occurred, marks a receipt, retries an action, or acquires ownership.

| Boundary | Start and ownership | Cancellation and outcome | Representative coverage |
| --- | --- | --- | --- |
| Critical Store I/O and native file edits | Caller holds the Store maintenance lease or canonical-path lock before dispatching native work. | Drain surviving work through repeated caller cancellation. Underlying failure wins; otherwise rethrow first caller cancellation. Already-completed futures need no task or loop turn. | `test_owned_operation_settlement.py`, `test_file_edit_native_cancellation.py`, `test_usage_cancellation_settlement.py` |
| Exclusive agent maintenance | Factory is invoked only after exclusive admission; child inherits the logical lease. | Same failure-first drain contract. The actual gate's finally releases ownership before the caller observes any result, error or deferred cancellation. A cancelled queued waiter must never invoke the factory. | `test_agent_maintenance.py`, `test_selective_restore_concurrency.py`, `test_owned_operation_settlement.py` |
| Download executor preparation/write/cleanup | Download owns only its private staging directory; publication has its own transaction. | Same failure-first native-job drain. Cleanup must not race a surviving writer. | `test_skill_download_cancellation.py` |
| Completion queue | Scope detaches its own metadata-only queue before flushing, preserving nested ownership. | Same drain loop, but notification bus errors remain best-effort inside `_emit`. No transcript conversion or new outcome policy belongs in the waiter. | `test_completion_delivery_cancellation.py`, `test_completion_event_failures.py` |
| Transactional manager persistence | Validated candidate generation is already installed under the manager's lock. | Write and authoritative readback must both settle. Reconcile or invalidate first; deferred cancellation then wins over write failure, retaining its cause. Execution receipts belong to the enclosing transaction when requested. Keep this policy separate. | `test_persistence_cancellation_hardening.py`, `test_generation_settlement.py`, `test_per_effect_receipts.py` |
| Native automation transaction | Global automation lock owns append, reload and conditional rollback after validation. | Failure-first externally, but an observer encapsulates failures to prevent HA shield logging private exception prose. Keep its observer and rollback policy explicit. | `test_native_automation_concurrency.py`, `test_native_helper_coverage.py` |
| Transient HA Script cleanup | Own pending stop followed by exactly one native unload/fallback stop. | Repeated cancellation cannot abandon native cleanup. Cancellation changes secondary-failure precedence and sanitizes warnings. `asyncio.wait` intentionally avoids HA shield's private failure logger. Keep separate. | `test_script_cleanup_settlement.py` in unit and genuine-HA suites |
| Skills filesystem transactions | Shared filesystem lock covers journal, recovery, publication and catalogue installation. | After caller cancellation, drain then consume secondary operation failure and rethrow first cancellation. Cancellable read leases are different again. Keep separate; no Skills architecture work in this batch. | `test_skill_transactions.py`, `test_skill_download_cancellation.py` |
| Backup transfer allocation/removal | Native allocation may return a path after caller cancellation; registry and staging owner retain cleanup authority. | Requires late-result deletion or enumeration, pending cleanup/backoff and preservation of original cancellation. Per-session retirement and every-session cleanup differ from a failure-first result helper. Keep separate. | `test_backup_transfer_recovery.py`, `test_restore_background_ownership.py` |
| Restore journal/recovery | Exclusive maintenance spans prepare/apply/commit decision/rollback and recovery-required state. | The outer owned-operation drain must not replace the transaction's own commit-vs-rollback evidence. Severe recovery failure remains authoritative. | `test_restore_atomicity_fault_injection.py`, genuine-HA atomicity/process-crash/responsiveness suites |
| Bash process teardown | Receipt starts before spawn; execution owns process group and both pipe readers. | Timeout uses forced cleanup; cancellation uses graceful bounded cleanup. Repeated cancellation must not abandon either surviving cleanup task. Successful teardown cannot establish whether arbitrary shell effects occurred. | Bash unit coverage and owned-cleanup regression |
| Provider/network/validation reads | No irreversible mutation has been admitted. Shared cached reads can have separate owners. | Remain promptly cancellable or retain their existing shared-task cache policy. Do not apply a durable-work waiter indiscriminately. | `test_conversation_cancellation_lifecycle.py`, active stream and resource-lifetime stress tests |
| Receipts, replay, composite and delayed execution | Precise effect dispatch, invocation/conversation lease and persisted job state each establish different authority. | Cancellation never proves rejection. Parallel results settle before their ordered error; serial paths fail fast. Independently scheduled work uses detached execution and fresh context. Preserve these specialized rules. | Replay equivalence/receipt matrices, deterministic cancellation, delayed persistence, genuine-HA continuation replay |

The shared failure-first waiter preserves the existing `ensure_future`/`shield`
mechanism and task/context inheritance. It adds no lock, context copy, timer,
serialization, retry or receipt transition. Callers must already own the whole
operation and must retain their resource until awaiting it completes. It is not
appropriate for ordinary cancellable reads or policies that need a late result
despite caller cancellation.

Private Store/file helper names can remain direct import aliases for established
internal consumers and test seams; they contain no wrappers or independent loops.
The maintenance gate remains a finally-only context manager: it does not choose
outcomes based on which exception unwinds its body. Its condition sections do not
hold the condition across suspended work (`wait_for` releases it while waiting).
