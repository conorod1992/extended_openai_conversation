# GitHub Actions execution policy

PR checks always creates the **PR validation** aggregate check. A reviewed path
policy in `ci/pr_workflows.json` selects reusable workflows; the aggregate requires
all selected workflows to succeed and all excluded workflows to be skipped.
Missing, failed, cancelled, or unexpected dependencies fail the check. Changes
to the selector or its policy run every lane. Checkouts validate GitHub's simulated
merge commit. The final check also rejects a base branch that advanced during the run.

Specialist rules can reference flat `dependency-groups` from
`ci/pr_dependency_groups.json` alongside lane-specific `paths`. Both sets of paths
are alternatives; branch restrictions and `paths-ignore` still apply. Unknown or
malformed groups reject selection. Editing the shared groups is a selector change
and therefore runs every lane, just like editing `pr_workflows.json`.

| Shared production dependencies | Specialist PR coverage |
| --- | --- |
| Frontend assets/build inputs and Management configuration/data contracts | Frontend/backend version skew |
| Integration Python, schemas and test environment | HA version upgrade; EOAI release upgrade |
| Integration package, HACS metadata and release tooling | HACS install/update |
| Provider clients, request processing and REST transport | IPv6-only networking |
| Frontend and integration runtime | Native HA browser compatibility |

Each specialist also selects changes to its own acceptance tests and supporting
harness. General collection does not substitute for these lanes: they supply the
environment flags and released/HA-version runtimes needed to execute the scenarios.
Documentation-only and unrelated unit-test diffs do not select these specialists.
`tests/test_pr_specialist_selection.py` exercises representative changed-file lists,
negative cases, branch filters and dependency-group validation. Update the reviewed
groups and these cases when introducing a new transport or UI/backend contract.

## Repository setting after rollout

Once this workflow is merged and **PR validation** has appeared in Actions, open
**Settings > Rules > Rulesets**, edit the ruleset targeting `develop`, enable
**Require status checks to pass**, add **PR validation**, and enable
**Require branches to be up to date before merging**. Save the ruleset. Replace
old individually required contexts with this aggregate; reusable workflow job
names have a caller prefix and irrelevant lanes intentionally skip. An already
green check cannot invalidate itself when the base advances after completion;
the up-to-date setting is therefore necessary. This policy uses strict status
checks; a merge queue would additionally require `merge_group` workflow support.

The small stable post-merge smoke remains enabled. Documentation deployment and
CI image publication retain their necessary push triggers; broad PR acceptance
suites no longer repeat on every merge into `develop`.

## Cadence

| Coverage | Routine cadence | Full Validation |
| --- | --- | --- |
| Normal Enhanced feature campaigns | All 19 nightly | Full heavy coverage |
| Heavy Enhanced and long-lifetime soak | Weekly | Full |
| Frontend latency | Nightly | Full |
| Latest released version upgrade | Nightly and relevant PRs | Full |
| Historical upgrade epochs | Weekly | All six epochs |
| Stable Real HA and HA dev acceptance | Nightly and relevant PRs | Full |
| Native HA browser | Stable/dev Chromium nightly; full matrix weekly | Full |
| Static cross-browser suite | Relevant PRs and weekly | Full |
| Mutation, native architecture, resource constraints | Weekly | Full |
| Recovery, IPv6, frontend/backend skew, HA upgrade, HACS lifecycle | Weekly and relevant PRs | Full |
| Android Companion | Narrowly relevant PRs and weekly | Full |
| iOS Companion | Manual | Full |
| Official HA Container | amd64 daily; both architectures weekly | Both |

Lint, typing, and unit coverage remain enabled. Python management dependencies
continue to select genuine HA frontend acceptance; mocked browser coverage uses
frontend and harness changes. Stable CI image builds track actual image inputs.
The stable public-journey PR matrix no longer duplicates the stable acceptance
job, and HA dev remains a scheduled compatibility signal.

Full Validation defaults to full coverage for release candidates and deliberate
comprehensive checks. For faster manual checks, its `quick` mode skips Enhanced
long-lifetime and iOS, along with the final release certification that requires
their evidence. Enhanced also offers `all-except-long-lifetime` independently.
See [Full validation](full-validation.md) for selection and reproduction commands.
Target individual workflows for specialist troubleshooting. Its coordinator still
waits on a runner; replacing that coordinator with asynchronous completion is a
separate change. Measure runner minutes and missed/late detections after rollout
before considering rotating normal Enhanced campaigns or less frequent latency
checks.
