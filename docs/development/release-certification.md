# Release certification

Release a commit that already contains its final `X.Y.Z` version in the manifest
and generated frontend version metadata. Merge that version change to `develop`,
run **Full validation** on that exact `develop` SHA. After it succeeds,
dispatch **Release** from `develop` with the same version. Full Validation runs
all non-live acceptance workflows, complete historical upgrades, supported SDKs,
heavy Enhanced campaigns, native mobile journeys, and both architectures, then
certifies that cohort. The release workflow performs its own packaged-install,
upgrade, and Firefox/WebKit checks before tagging the same source SHA. It does
not create a version commit, and there is no certification override.

`ci/release_certification.py` requires a successful exact-source Full Validation
parent on `develop` and rechecks its recorded child run IDs and attempts. Children
must have succeeded on that parent's temporary candidate ref. The programme
certificate must refer to those same runs and pass environment and execution
checks. Cleanup of the temporary ref does not invalidate the recorded evidence.
A different candidate, missing workflow, expired artifact, or stale child attempt
fails closed. The previous exact-source individual `develop` dispatch route
remains supported when no successful Full Validation parent exists; routine
post-merge workflows no longer provide comprehensive release evidence.

The gate also downloads and rechecks the enhanced final certificate, including
source-bound case ledgers, reviewed mandatory cases and semantic minimums. Green
job names alone are insufficient. Required upgrade lanes are `latest`, `6.8.2`,
`6.7.0`, `6.5.0`, `6.3.1` and `6.2.0`, using the existing released-payload migration
journeys. Each successful lane must publish exact-candidate evidence and the
actual released source version/commit. The existing 6.2.0 specialised Function
Tool omission remains documented in the workflow; its config-flow/Assist/browser
migration journeys remain mandatory.

Supported SDK lanes are `2.21.0`, `2.45.0` and `3.10.0`; these match the manifest's
reviewed floor and ceiling. Each needs a successful job and an envelope recording
that actual installed SDK on the candidate SHA. `latest-3x-early-warning` remains
advisory beyond that ceiling and cannot replace a supported lane. These are offline
protocol tests; no live OpenAI calls are required. Workflow policy unit tests require
review when migration epochs, supported SDK lanes or manifest bounds change.
Expired/missing artifacts fail closed and require a fresh exact-source run.

Official Home Assistant Container certification is architecture-bound. The amd64
lane runs daily and the native ARM64 lane runs weekly; a manual dispatch runs
both. Release certification requires successful amd64 and ARM64 jobs plus their
separate exact-source evidence artifacts on the release SHA, so a recent run on
another commit or only one architecture cannot certify a release.

# Performance regression contracts

The browser suite checks Overview, configuration load, and save with 1 and 50
agents. It limits management round trips, duplicate asset requests, and
unrelated validation or agent refresh work. The Python management catalog test
uses 50 agents and asserts zero Store loads and tool validations during initial
navigation. The frontend build reports the production JS, CSS, and entry sizes
and fails only after roughly 25–35% growth above the 2026-09-27 baseline.
These structural checks avoid shared-runner wall-clock gates; the existing
large-installation nightly records setup, population, backup, and per-Assist
timings and asserts one model handler call per public turn through its tool and
Request Rule-heavy fixture.

# Production incident regression rule

Every reproducible user-reported production defect should, where practical,
become a permanent anonymized regression test. Reproduce the original
configuration, HA state, action sequence, and user-visible failure first;
strip private data from the fixture; verify the test fails on the buggy behavior;
then fix production code and retain the test. Prefer a realistic public journey
over a narrowly mocked unit case when feasible.
