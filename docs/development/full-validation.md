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

## Completion without idle runners

The dispatch workflow creates an isolated `full-validation-RUN-ATTEMPT` branch,
launches the selected children, records their IDs in a pending manifest and
exits. Its success only means dispatch succeeded. Follow the **Full validation
results** check on the candidate commit for the actual result.

`full-validation-collect.yml` handles completion events. Each invocation checks
the recorded children once and exits if any are still running. Once all tests
pass, it dispatches programme certification once; its completion event finishes
the cohort. A repository-wide collector queue serializes callbacks before
allocating runners. Child source SHA, workflow, branch and run attempt must
match the manifest. Failures and cancellation produce a failed result.

The listener must exist on the default branch for automatic completion events
to run. Before that merge, candidate dispatches cannot finalize automatically.
After installation, a missed event can be recovered without rerunning tests:

```sh
gh workflow run full-validation-collect.yml -f parent_run_id=DISPATCH_RUN_ID
```

The parent uploads a pending `full-validation-cohort` manifest. The collector
uploads `full-validation-result-RUN-ATTEMPT` with the completed proof, then
publishes the result check and deletes the temporary branch. Release
certification verifies the successful collector workflow, parent/attempt
binding, exact child inventory and attempts, and programme evidence. A pending
dispatch, quick cohort, missing artifact, failed collector, expired evidence or
superseded child attempt cannot authorize a release. Legacy completed cohorts
remain readable.

If collection fails before recording a result, fix the issue and use the
recovery command. The temporary ref is retained for inspection until completion.
Rerunning a child attempt changes its provenance: dispatch a new cohort instead
of silently replacing a recorded attempt.

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
