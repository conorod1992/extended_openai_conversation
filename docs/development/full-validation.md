# Full validation

Manual validation defaults to **quick**. It runs the ordinary workflows and
heavy Enhanced campaigns, excluding iOS, long-lifetime soaks, generator proofs
and release certification. Use this mode while iterating. A successful quick
run cannot certify a release.

Select **full** explicitly for a settled release candidate. It retains native
iOS acceptance, both long-lifetime partitions and the full generator proofs.
After all 28 test workflows pass, programme certification checks both official
Container architectures and the complete candidate-bound execution evidence.
The stable HA version comes from this cohort's Enhanced certification artifact.
HA dev is resolved once and passed to every dev consumer as an immutable SHA.

```sh
gh workflow run full-validation.yml -f mode=quick
gh workflow run full-validation.yml -f mode=full -f seed=42 -f ha_dev_sha=FULL_SHA
```

## Overall result

The **Full validation result** job creates an isolated
`full-validation-RUN-ATTEMPT` branch, dispatches all selected children and waits
for their terminal results. The parent remains running while tests run. It
turns green only when every selected workflow succeeds, including programme
certification in full mode. Failed launches, failed tests, cancellations and
unavailable child status fail the parent. Its summary links every child and
lists intentional quick-mode exclusions. The parent has a six-hour deadline;
a timeout cannot be mistaken for a passing validation.

The parent uploads the completed `full-validation-cohort` proof used by release
certification. Temporary refs are removed after completed cohorts; interrupted
runs retain them for diagnosis. Quick runs never authorize a release.

Full mode dispatches iOS and Enhanced first, then gives both heavy lifetime
partitions up to ten minutes to start before dispatching the shorter workflows.
Enhanced likewise uses a separate lifetime matrix and a start observer before
releasing its remaining jobs. The observer waits for starts, not successful
completion; the 30-minute reload and uninterrupted processes run concurrently
with the rest of validation. Quick, daily, and browser-only selections retain
their existing inventories and do not wait for unselected lifetime work.

This head start is a scheduling hint only. An unavailable status API or bounded
wait expiry releases all remaining tests. Reads use the recorded attempt and
candidate SHA. Completed failures also release the fan-out, while the existing
job-result, execution-inventory, environment, duration, and final certification
checks continue to reject missing or failed lifetime coverage. The legacy
asynchronous dispatcher leaves this scheduling hint to Enhanced itself.

`full-validation-collect.yml` remains available to recover older asynchronous
cohorts. It identifies the parent by its workflow path, since a generated run
title is not the workflow name. Recovery publishes the **Full validation
results** check, a result table and a terminal artifact; unsuccessful validation
also fails the collector job. The listener must be on the default branch for
automatic completion callbacks. Missed callbacks can be recovered manually:

```sh
gh workflow run full-validation-collect.yml -f parent_run_id=DISPATCH_RUN_ID
```

Rerunning a child changes its provenance. Dispatch a new cohort instead of
silently replacing a recorded attempt.

## Test ownership and cadence

Daily Enhanced coverage retains generated configurations exercised through real
HA and the SDK, plus the cheap field/enum classification smoke test. The full
fixed-seed generator completeness/reproducibility proofs run weekly, on PRs
that change their inputs, on explicit dispatch, and in full candidate validation.
They run once, independently of the normal/heavy campaign multiplier.

`ci/campaign_owners.json` assigns overlapping files to one same-environment
campaign. The reviewed execution contract assigns their semantic counters to
that owner too. Standalone campaigns run their owned cases; consult this map
when selecting specialists (for example, restore recovery belongs to
`persistence`, process-kill restart tests to `process-chaos`).

The programme coverage guard preserves the pre-consolidation union of mandatory
cases and all semantic minimums. Different HA versions, browser engines, and
reload/uninterrupted lifetime processes remain separate coverage boundaries.
Long-lifetime partitions remain scheduled weekly; daily Enhanced excludes them.
iOS full acceptance runs weekly, on native-input pushes, explicit dispatch and
full candidate validation. Build-only warming remains available with
`warm_only=true`, alongside the existing exact native artifact reuse.

Live OpenAI workflows remain separate because they call paid external providers.
See [Actions execution policy](actions-policy.md) for PR selection and cadence.
