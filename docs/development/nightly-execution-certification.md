# Nightly execution certification

Enhanced runs upload structural pytest and Playwright execution ledgers, including
ordinary genuine-HA tests without stress traces. Each ledger records collected
node IDs, whether execution began, final outcomes and pytest phase outcomes.
Teardown failures are failures. Bodies, locals, errors and skip reasons are omitted.
Existing artifact sanitisation still applies.

`tests_stress/nightly_execution_contract.json` is the single reviewed case catalog.
Python campaign membership reuses the enhanced workflow selectors. Browser IDs
include the engine. Certification compares the catalog with actual execution;
missing/deselected/skipped/xfailing/unexpected-passing cases cannot certify a run.
New selected Python functions and unreviewed collected cases fail review guards.
Diagnostic campaigns intentionally test failures and do not grant full certification.

Conditional fixture copies may skip only under an explicit allowance. Their genuine
HA invocation must still pass (`requires_pass: true`). Saved Request Rule wording/defaults now execute through the genuine HA bridge
as well as the fixture campaign. A genuine exception must record exact node ID, allowed outcomes, review link, reason and an
expiry; `requires_pass: false` explicitly acknowledges that exception. No campaign
has a blanket prerequisite-based skip allowance. Expired allowances fail closed.

Critical campaigns require small nonzero semantic totals from existing traces.
Only successfully completed scenarios contribute semantic counts. The certificate
contains exact execution cases, totals and rejection reasons in addition to the
actual GitHub job conclusions.

For intentional test renames/parameter changes, collect the complete workflow
Python selector union with `ENHANCED_EXECUTION_EVIDENCE=1`,
`PYTEST_PLUGINS=ci.pytest_execution`, and an isolated `STRESS_ARTIFACT_DIR`, using
`pytest --collect-only`. Collect Playwright with `--list` for the stress config,
curated Firefox/WebKit configs, genuine-HA specs selected by the browser Python
harness, and the retained-mutation/reconnect/ownership recheck specs. Collection
requires the same supported HA environment as CI; it does not execute tests.

Run `python ci/update_execution_contract.py --pytest LEDGER --browser LEDGER...`.
Inspect the resulting catalog diff. This utility preserves reviewed exceptions and
minimums; it never approves a skip. A smaller catalog is a coverage change requiring
review, not an automatic consequence of a successful job.

The Real-HA inventory separately classifies full-file selection, node-only partial
selection and excluded files. Those guards remain enforced; execution evidence
adds case-level certification rather than replacing selection governance.

The preparation job checks out the workflow invocation's immutable `github.sha`
and publishes `candidate_sha`. Python, Chromium, Firefox/WebKit, HA lifecycle
lanes and the final certification job all check out that exact output. They never
resolve a moving branch independently. Certification requires each job envelope
and each pytest/Playwright execution ledger to identify this intended SHA; mutual
agreement on a different commit is insufficient. Cases link to their ledger IDs.
The final index records intended candidate and tested identities separately.

Python evidence records key HA/OpenAI SDK/HTTPX/aiohttp/HA fixture versions,
Python/platform/architecture, the HA source commit for VCS installations, and the
container image tag plus its prebuilt dependency fingerprint where available.
An image digest is recorded when supplied by the environment. Playwright ledgers
record Node and Playwright versions. Canonical environment fingerprints accompany
these compact records; installation URLs, local paths and full dependency dumps
are omitted. Unknown checkout identity cannot fall back to invocation metadata.

The saved exploration corpus compares production EOAI continuity, actual HA Store
I/O, native file replacement and public AI Task service effects with independent
expected-state models. The models alone never constitute runtime evidence. Replays
retain their operation order; the AI Task trace records actual wire/service events.
A sensitivity witness removes the file conflict boundary and must fail the replay.

`reviewed_valid_coverage.json` freezes the feasible configuration obligation set and
the exclusion-reason counts, independently of the generator's chosen covering set.
The guard rejects disappeared obligations and changed exclusions. To review an
intentional change, run `python -m ci.review_valid_coverage` and inspect the change
before updating the snapshot; this command does not approve a new baseline.

The Nightly programme certification workflow accepts an exact candidate SHA and
resolved stable HA version. It requires successful runs of the reviewed required
workflow inventory on that SHA, rechecks Enhanced execution certificates, the
supported SDK lanes, migration-boundary upgrade lanes and native browser evidence.
Stable evidence must match the supplied HA version, the oldest lane must match the
repository floor, and dev evidence must share an immutable HA Core snapshot.
Missing or expired artifacts fail certification. Different SDK/upgrade matrix
points keep their own reviewed identities rather than sharing one fingerprint.
The resulting `nightly-programme.json` lists run URLs, identities and rejection
reasons. Run it after the required jobs complete; it does not trigger absent runs.


Function outcomes are checked against independently specified backend and consumer
shapes across direct execution, provider execution, composites, Request Rules and
native AI Tasks. Seven business values include null, false, zero, empty containers
and error-shaped successful data. Real dependency failures and native tool error
contracts remain distinct from business results. HTTP 404 is a transport failure;
an HTTP-successful error-shaped body is business data.

Durable-store schedules compare acknowledged operations, retained live state and
an independently reloaded store against an allowed-outcome model. Events control
commit, acknowledgement loss, queued successors and cancellation. Assist is the
first recovery consumer for Memory and Knowledge after restoration, without a
manager getter, settings page or reload healing the runtime first. Real stalled
HTTP requests exercise deadlines rather than raising synthetic timeout errors.
Retained prompt/API journeys preserve completed calls, summaries and attachments;
attachment budgets account for the complete retained request before dispatch.
