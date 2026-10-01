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
