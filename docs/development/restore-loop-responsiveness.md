# Restore event-loop responsiveness

## Confirmed scope and reproduction

The reported event-loop starvation is present on current develop `baa721a` (the
merge of PR #1261), not only the original reported `3ec6748`.

`tests/test_restore_loop_responsiveness.py` creates retained records through the
actual Usage run/request APIs with nonzero tokens, then restores the unchanged
exported categories through the public backup restore coordinator. The reduced
fixture uses asynchronous worker-backed storage copies and stand-ins for unrelated
managers; it is not a claim of a local genuine-HA WebSocket execution.

With normal loop settings and a 5ms unrelated heartbeat, the initial isolated
2,000-request runs blocked for **566ms and 550ms**, against a **16ms empty control**.
After the worker boundary, prepared restores measured **55ms and 43ms**, and legacy
document restores **42ms and 73ms**. These are responsiveness measurements, not a
claim that total restore duration decreases. Host scheduling changes exact values.
The genuine-HA test retains a 250ms latency ceiling. Unit tests record timing but
assert worker placement deterministically: coverage and asyncio debug tracing can
amplify scheduling gaps without changing the worker boundary. The genuine-HA test
is assigned to the serial shard without coverage or xdist contention.

The identical final seven cases produce **five failures and two passing empty
controls** on an untouched `baa721a` checkout. The failures are the four large
prepared/legacy worker-placement cases and the Usage-worker validation boundary. No production
source changes, artificial processing delays, debug-loop amplification or increased
timeouts are involved in that before/after replay.

## Bounded correction

- Serialize completed Usage request/run records in a worker from locally captured
  membership, retaining manager/storage locks and the existing publication order.
- Validate large Usage sections outside the loop during rollback collection,
  backup inspection and full/custom transfer preparation. Check envelope and byte
  limits before schema processing. HA configuration/Function/Rule validation stays
  in its original loop context, including `cv.template`.
- Copy detached transfer data, apply data-only ownership mappings, build journal
  documents, copy journal write data, compare verified journal contents and fingerprint
  full preview snapshots in workers. No live manager mutation or durable storage call moves to a worker.
- Keep maintenance ownership, backup locking, cancellation shielding, write-ahead
  verification, category apply order, commit verification and rollback unchanged.
  Synchronous inspection helpers remain available for existing synchronous callers.

## Validation

The related Usage, restore, rollback/fault-injection, redaction, selective-transfer,
ownership, validation-context and maintenance selection passes **824 tests** locally.
Six existing Windows transfer-staging failures remain (`fcntl` unavailable), and
`test_transfer_staging.py` cannot be collected on this host; these are not presented
as passing runtime validation. Ruff checks/format and MyPy pass all 146 source files.
The additional configuration-copy control requires loop context during ownership
mapping. Existing race/cancellation test doubles now mock the async mapping boundary;
all their assertions remain intact. No skip policy is weakened.

`tests_real_ha/test_restore_loop_responsiveness.py` uses a genuine authenticated HA
WebSocket client, EOAI export/upload/preview/restore commands, an empty control and
2,000 real Usage requests/runs. Upload and preview finish before measurement; the
restore request contains only the session ID and preview token. It checks the
heartbeat and exact restored counters. This acceptance test runs in the repository's
Linux real-HA CI; WSL/HA's Linux test runtime was unavailable locally. Linux CI status
and any additional review corrections are recorded in the PR.

The two public-route cases in `tests/test_restore_preview_worker.py` also fail on
untouched `baa721a` and pass with the fix. They require the full snapshot fingerprint
to run in a worker and prevent a cancellation delivered during hashing from consuming
an expired upload or committing data. The seven replay cases plus these two give
**seven failures and two empty controls** before correction. The additional
configuration-copy context control passes afterward as well.
