# GitHub Actions execution policy

PR checks always creates the **PR validation** aggregate check. A reviewed path
policy in `ci/pr_workflows.json` selects reusable workflows; the aggregate requires
all selected workflows to succeed and all excluded workflows to be skipped.
Missing, failed, cancelled, or unexpected dependencies fail the check. Changes
to the selector or its policy run every lane. Checkouts validate GitHub's simulated
merge commit. The final check also rejects a base branch that advanced during the run.

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

Full Validation is for release candidates and deliberate comprehensive checks.
Target individual workflows for specialist troubleshooting. Its coordinator still
waits on a runner; replacing that coordinator with asynchronous completion is a
separate change. Measure runner minutes and missed/late detections after rollout
before considering rotating normal Enhanced campaigns or less frequent latency
checks.
