# Release certification

Release a commit that already contains its final `X.Y.Z` version in the manifest
and generated frontend version metadata. Merge that version change to `develop`,
allow ordinary workflows to finish, and run **Enhanced nightly acceptance and
stress** with `campaign=all` and `intensity=heavy` on the exact `develop` SHA.
Also dispatch **Release Upgrade Acceptance** with `from_version=all` and
**OpenAI SDK Compatibility** on that same candidate. Then dispatch **Release**
from `develop` with the same version. The release
workflow runs its upgrade, packaged-install, and Firefox/WebKit validations
before creating a tag on that same source SHA. It does not create a version
commit. There is no emergency certification override.

`ci/release_certification.py` checks GitHub Actions metadata for successful runs
on the exact source SHA from `ci.yml`, `frontend.yml`,
`cross-browser-smoke.yml`, `real-ha.yml`, `release-smoke.yml`, and
`enhanced-stress.yml`, `upgrade-acceptance.yml`, and
`openai-sdk-compatibility.yml`. Runs must identify `develop` and the exact source;
an ancestor, descendant or another branch cannot substitute. The nightly run must
be an explicit workflow dispatch
with every heavy campaign, browser engine, HA lifecycle point, and consolidated
certification job successful. A passing parent SHA or a diagnostic-only run does
not qualify. Frontend and cross-browser checks run on every `develop` push so
the release gate can bind their results to the release source.

The gate also downloads and rechecks the enhanced final certificate, including
source-bound case ledgers, reviewed mandatory cases and semantic minimums. Green
job names alone are insufficient. Required upgrade lanes are `latest`, `6.8.2`,
`6.5.0`, `6.3.1` and `6.2.0`, using the existing released-payload migration
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
