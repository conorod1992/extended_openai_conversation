# Backup / restore / persistence test consolidation

Base: `d0e26ebae75cacae7bd9ae036ab6414f23678801` (`develop`). Created directly from fetched `develop`, then rebased after #637 merged. No unmerged branch dependency. Production, CI, shared fixtures, Real HA, HA tools/actions/service-handler and other excluded feature tests are unchanged.

## Audit findings

The historical files mostly protect unique contracts. This change retires nine historical files, removes five proved duplicates, and combines one identical byte-limit validation boundary. It preserves failure location and timing rather than equating tests by final state. [The per-test inventory](backup-persistence-test-inventory.md) records every candidate's invariant, calls, failure setup and disposition.

- Backup validation retains schema/version migration, serialization, redaction, optional sections, limits, manager ordering and snapshots. Credential redaction, snapshot consistency and value-level partial-restore atomicity remain dedicated owners.
- Transfer validation now owns archive/manifest/JSON validation and request guards; lifecycle owns staging, registration, inspection, consumption and cleanup; recovery owns executor/caller cancellation and scalable journal loading. Export and import, unavailable files and bounded reads after a misleading stat, pre-allocation and post-allocation registration failure remain separate.
- Restore validation owns corrupt journal envelopes, embedded payloads and persisted Core shapes. Recovery owns verified journal writes, restart/rollback/retry and configuration durability. Agent lookup owns exact resolution and runtime registry reconciliation. The reset with no durable-manager tuple is retained separately from reset with a tuple and a non-fallback usage pointer.
- Durable archive tests retain first-journal failures for insertion versus deletion, post-journal partition failure with restart, obsolete-partition cleanup failure, pruning, active-map cleanup and ownership checks. Storage I/O owns mode repair alongside atomic replacement. Cancellation of the underlying Store task stays separate from caller cancellation while a successful or failed save is shielded.
- `test_final_lifecycle_persistence_hardening.py` is retained: its update/index rollback contracts are distinct from add/create cancellation, and its template unload/reference and SDK compatibility cases are outside this refactor. No forced rename or unrelated feature move.
- Adjacent compatibility fixtures, manager initialization/ownership, selective restore concurrency, secret restore context, fault-injection suites, usage durability, and the four portable-transfer modules are audited and retained. `test_transfer_residual_coverage.py` retains portable section/secret/dependency validation; it is not equivalent to chunked backup transport. Archive search/model, general memory integrity, function configuration, delayed-tool scheduling and guest-mode feature suites remain feature-owned and are exercised by the full run.

## Deleted-test replacement proof

| Removed function | Surviving function | Same boundary and stronger evidence |
| --- | --- | --- |
| `test_import_rejects_quota_before_allocating_file` | `test_import_quota_is_checked_before_temp_file` (`test_backup_transfer_limits.py`) | Both start with empty registries, quota 4 and requested size 5, call `_start_import`, and assert quota rejection and empty imports. Survivor also makes the allocator fail if invoked and asserts it never ran. No file or in-memory session can be created. |
| `test_cleanup_expired_detaches_and_deletes_both_session_types` | `test_expired_export_and_import_sessions_are_detached_and_deleted` (`test_backup_transfer_lifecycle.py`) | Both seed expired export/import files and registry entries, call the same lazy cleanup, and assert both registries and files are gone. Survivor uses nonempty file contents. Expiry values are both strictly in the past; no crash/cancellation injection is lost. |
| `test_import_chunk_cancellation_discards_staging_session` | `test_cancelled_import_chunk_discards_partial_session` (`test_backup_transfer.py`) | Both begin at received/index 0, inject `CancelledError` at `_async_append_file`, require propagation, and check registry detachment and file deletion. Survivor creates staging through the real `_start_import` path instead of manually constructing an equivalent empty session. Executor-completion cancellation tests remain separate. |
| `test_import_chunk_rejects_already_complete_session` | `test_import_chunk_rejects_missing_wrong_owner_and_completed_upload` (`test_backup_transfer_validation.py`) | Both seed a complete upload, submit the expected next index and a valid nonempty encoded chunk, and require the same pre-write complete-upload rejection. Byte count/index fixture values differ but reach the same guard. Survivor also covers missing sessions and wrong owner; no append or commit is attempted. |
| `test_archive_commit_does_not_publish_ram_before_journal_write` | `test_archive_journal_failure_does_not_publish_candidate_state` (`test_persistence_failure_boundaries.py`) | Both seed an old session/turn/partition and propose a different month, then fail the first metadata intent write. Survivor checks identity of every prior in-memory container, active mapping, unchanged empty pending state and no partition writes. Folded an explicit single metadata-attempt assertion, preserving the older write-order evidence. Both storage doubles raise before persisting candidate metadata. |

`test_inspect_backup_rejects_limit_above_global_cap` is merged into `test_inspect_backup_rejects_invalid_byte_limits`: add `MAX_BACKUP_BYTES + 1` to the same `ValueError` matrix. One fewer function, same five collected validation cases; no boundary removed.

No rollback/retry, restart/live mutation, exception/cancellation, migration/corruption, or import/export boundaries were merged. In particular, live apply+rollback failure, committed forward completion, unverified commit decision, interrupted rollback, post-commit cleanup retry, seven manager-boundary failures and detached value-level rollback all remain.

## Reduction summary

- 111 historical survivor functions relocated: 106 have identical test ASTs after normalizing fixture names; one only combines nested `with` statements for lint. Thus 107 move without behavioral changes.
- Three fixture corrections: two schema-validation tests now run inside an event loop; ZIP-member validation assigns the raw filename explicitly so Windows cannot normalize away the unsafe input. Same assertions/contracts, now independently executable.
- One relocated matrix gains the upper-bound case from one merged function.
- Five genuinely redundant functions/cases removed; one additional function merged with no case loss.
- One assertion folded into the stronger journal-failure survivor: exactly one metadata write attempt before any partition write.

| Metric | Before | After |
| --- | ---: | ---: |
| Family files | 35 | 28 |
| Test functions | 261 | 255 |
| Collected family cases | 386 | 381 |
| Focused runtime (local sample) | 15.44 s | 14.77 s |
| Full local runtime (sample) | 43.27 s | 39.77 s |

No runtime improvement claim: these are single local samples with environmental failures, not stable-runner performance evidence.

## Historical-file mapping

| Retired file | Semantic owner(s) |
| --- | --- |
| `test_backup_coverage.py` | `test_backup.py` |
| `test_backup_transfer_coverage.py` | `test_backup_transfer_validation.py`, `test_backup_transfer_lifecycle.py` |
| `test_backup_transfer_remaining_coverage.py` | transfer validation, lifecycle and recovery |
| `test_backup_transfer_residual_coverage.py` | transfer validation, lifecycle and recovery |
| `test_restore_recovery_coverage.py` | `test_restore_recovery_validation.py`, `test_restore_recovery.py`, `test_restore_recovery_agent_lookup.py` |
| `test_restore_recovery_remaining_coverage.py` | restore validation, recovery and agent lookup |
| `test_durable_state_hardening_coverage.py` | `test_durable_state_hardening.py`; deleted journal case maps to persistence failure boundaries above |
| `test_persistence_hardening_coverage.py` | `test_storage_file_io_hardening.py` |
| `test_persistence_hardening_cancellation_coverage.py` | `test_persistence_cancellation_hardening.py` |

## Exact coverage guard

The before/after focused and full local runs each have identical statement counts, missed-statement counts, branch counts, missed-branch counts, **and exact missing line/branch sets for all 123 measured production modules**. There are no coverage losses in the controlled comparison. [Machine-readable evidence](backup-persistence-coverage.json) contains affected-module missing sets and hashes of the full 123-module comparison; hashes cover counts and sets, not percentages.

Focused family metrics (counts are unchanged; arrows show before → after):

| Module | Statements | Missed statements | Branches | Missed branches |
| --- | ---: | ---: | ---: | ---: |
| `agent_config.py` | 412 | 144 → 144 | 198 | 98 → 98 |
| `backup.py` | 162 | 0 → 0 | 32 | 0 → 0 |
| `backup_transfer.py` | 579 | 4 → 4 | 156 | 3 → 3 |
| `conversation.py` | 1020 | 849 → 849 | 350 | 348 → 348 |
| `conversation_archive.py` | 467 | 144 → 144 | 120 | 63 → 63 |
| `delayed_tools.py` | 305 | 228 → 228 | 96 | 96 → 96 |
| `functions/file.py` | 187 | 125 → 125 | 46 | 44 → 44 |
| `guest_mode.py` | 374 | 247 → 247 | 110 | 100 → 100 |
| `knowledge.py` | 458 | 121 → 121 | 138 | 63 → 63 |
| `memory.py` | 944 | 367 → 367 | 360 | 212 → 212 |
| `persistence_hardening.py` | 17 | 0 → 0 | 4 | 0 → 0 |
| `request_rules.py` | 1019 | 531 → 531 | 458 | 306 → 306 |
| `restore_recovery.py` | 227 | 1 → 1 | 58 | 3 → 3 |
| `secret_redaction.py` | 73 | 1 → 1 | 34 | 2 → 2 |
| `temporary_memory.py` | 440 | 161 → 161 | 140 | 77 → 77 |
| `transfer.py` | 432 | 2 → 2 | 184 | 1 → 1 |
| `usage.py` | 624 | 301 → 301 | 190 | 144 → 144 |

Full ordinary local run:

| Module | Statements | Missed statements | Branches | Missed branches |
| --- | ---: | ---: | ---: | ---: |
| `agent_config.py` | 412 | 1 → 1 | 198 | 1 → 1 |
| `backup.py` | 162 | 0 → 0 | 32 | 0 → 0 |
| `backup_transfer.py` | 579 | 4 → 4 | 156 | 3 → 3 |
| `conversation.py` | 1020 | 9 → 9 | 350 | 11 → 11 |
| `conversation_archive.py` | 467 | 4 → 4 | 120 | 4 → 4 |
| `delayed_tools.py` | 305 | 2 → 2 | 96 | 3 → 3 |
| `functions/file.py` | 187 | 0 → 0 | 46 | 0 → 0 |
| `guest_mode.py` | 374 | 2 → 2 | 110 | 3 → 3 |
| `knowledge.py` | 458 | 4 → 4 | 138 | 4 → 4 |
| `memory.py` | 944 | 2 → 2 | 360 | 0 → 0 |
| `persistence_hardening.py` | 17 | 0 → 0 | 4 | 0 → 0 |
| `request_rules.py` | 1019 | 3 → 3 | 458 | 1 → 1 |
| `restore_recovery.py` | 227 | 1 → 1 | 58 | 3 → 3 |
| `secret_redaction.py` | 73 | 0 → 0 | 34 | 1 → 1 |
| `temporary_memory.py` | 440 | 2 → 2 | 140 | 0 → 0 |
| `transfer.py` | 432 | 2 → 2 | 184 | 0 → 0 |
| `usage.py` | 624 | 1 → 1 | 190 | 3 → 3 |

## Validation and limitations

Local environment: Python 3.14.2, Home Assistant 2026.8.0b3, Windows. Disable plugin autoload and explicitly load `pytest_asyncio.plugin`, `pytest_cov.plugin`, `pytest_timeout`; full runs also load `xdist.plugin` with `-n 2 --dist=loadscope`. Coverage arguments: `--cov=custom_components/extended_openai_conversation_responses --cov-branch --cov-report=json --cov-fail-under=0`. The zero percentage threshold is only for focused measurements; the guard is exact set comparison. No repository configuration changed.

The uncorrected initial baseline was 323 passed / 5 failed / 1 skipped for the narrower 31-file family. Two failures were the order-dependent synchronous schema tests and one was Windows ZIP normalization. Before comparing the reduction, the isolated current-develop control receives exactly the same three fixture corrections as the candidate. The corrected expanded family is 383 → 378 passed, with the same two POSIX permission failures and one SDK-version skip on both sides. Both permission contracts remain intact and unskipped in the repository; Linux CI is required to validate them.

Full local attempts: before 4074 passed, 19 failed, 2 skipped, 2 collection errors; after 4070 passed, 18 failed, 2 skipped, 2 collection errors. Windows shell/path/permissions, missing optional media imports and local HA differences prevent claiming a stable full pass. The varying failure is the unchanged edit-file Unicode-content test, not a changed persistence case. Exact coverage still matches across all modules. Existing Linux CI is used for stable and automatically triggered HA-dev compatibility results.

Ruff check and format pass for every changed test and all production modules. `mypy --platform linux custom_components/extended_openai_conversation_responses` passes all 123 files; native Windows mypy reports the pre-existing Unix signal/process API mismatch. AST migration audit accepts only the five explicitly described non-identical moved test bodies. `git diff --check` passes. No configured mutation campaign targets these backup/restore/persistence modules, so no mutation infrastructure was added.

## Safety rationale

Preserved: backup schema/serialization and migration/version handling; all credential-redaction cases; transfer lifecycle, size/resource limits and section dependencies; atomic writes, rollback and cleanup; cancellation propagation and ownership release; restart and corrupt-state recovery; retry semantics; owner/state isolation and durable/in-memory consistency. Unique cases move without collapsing their failure phases. This is test-only behavior: no production code, CI workflow or shared test infrastructure changes.
