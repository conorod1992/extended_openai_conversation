# Targeted mutation testing

This repository uses Mutmut for deliberately small, manual mutation-testing campaigns around security- and correctness-sensitive decision boundaries.

## Campaigns

The manual workflow provides four selectable campaigns:

### `function-tools`

The existing default campaign. It mutates critical Function Tool execution logic in:

- `function_call_budget.py`
- `function_tool_resolution.py`
- `function_tool_recovery.py`
- `parallel_tool_execution.py`

It runs the existing focused Function Tool mutation-contract test set.

The runner keeps the original unfiltered selection within this four-module allowlist. Mutmut 3.7.0 generates no mutants for the decorated `FunctionCallBudget` class, so the runner checks that the campaign generates mutants rather than incorrectly requiring mutants from every configured file. This does not claim mutation coverage of that decorated class.

### `guest-security`

This campaign runs `tests/test_guest_mode_mutation.py` and mutates the small module-level `resolve_guest_policy()` routing boundary in `guest_mode.py`.

The focused tests also assert the resolved `GuestCapabilityPolicy` membership behaviour directly: unrestricted versus explicit entity/tool sets, independent read/control entity boundaries, exact configured-tool membership, and fail-closed empty capability sets. Those dataclass methods remain valuable normal regression tests, but Mutmut 3.7.0 does not reliably mutate methods on decorated classes such as `@dataclass`, so the mutation target is deliberately the supported module-level policy resolver rather than pretending those methods are being mutated.

`resolve_guest_policy()` is the security-relevant decision point that determines whether Guest Mode is inactive, uses the current exclusion-policy resolver, or falls back to the legacy resolver. The wider Guest Mode implementation remains outside this targeted campaign.

### `ha-permissions`

This campaign mutates the basic Home Assistant permission boundaries in `ha_permissions.py` and runs `tests/test_ha_permissions_mutation.py`.

The current mutation targets are deliberately limited to:

- filtering model-visible entities for an authenticated user's Home Assistant READ permissions; and
- requiring Home Assistant CONTROL permission for already-resolved entity targets.

This is intentionally **not** a claim that every advanced Function Tool, HA LLM Tool, custom function, indirect target-resolution path, or other execution mechanism is universally constrained by these two routes. Those paths need their own explicit security contracts before mutation tests should make stronger assertions about them.

### `request-rules`

The `request-rules` campaign selects exactly these functions in `custom_components/extended_openai_conversation_responses/request_rules.py`:

- `RequestRules.match`: strict selection, sentence results, and fuzzy fallback/threshold/ranking;
- `RequestRules._sort_and_compile`: enabled-rule participation, effective matching settings, and strict ranking;
- `_deterministic_match`: whole-phrase boundaries for equals, starts-with, ends-with, and contains.

`tests/test_request_rule_matching_contracts.py` uses the public manager methods with an in-memory store. It exercises real validation, compilation and matching. Strict precedence is match type (equals, sentence pattern, starts-with, ends-with, contains), then longer phrase, then rule order. This is not first-in-list matching. Fuzzy candidates are considered only after strict matching fails, with inclusive thresholds and score/type/order precedence. Tests also cover disabled and absent rules, nonmatching candidates, phrase variants, inherited/custom normalization settings, and sentence captures without tolerant text transformations.

The compilation helper also contains storage diagnostics and pattern activation bounds. Those branches are not the objective of this campaign. Do not assert exact diagnostics, persistence bookkeeping, cache layout, or compiled-state counts just to kill their mutants. The sentence grammar engine, fuzzy similarity algorithm, normalization implementation, async scheduling, routing overrides, actions, providers, Function Tools, Guest Mode, permissions and UI are deliberately not Request Rules mutation targets. Calling some of them during test setup does not expand that claim.

Mutation testing complements normal unit/property tests and real-Home-Assistant acceptance tests; it is not a replacement for either. No production behaviour is changed for this campaign.

## Scope principles

Mutation testing here is selective rather than repository-wide. A campaign should target a small decision boundary only when its expected behaviour is clear enough to support reliable assertions.

Mutation testing complements the normal unit/property tests and real-Home-Assistant acceptance tests; it is not a replacement for either. The real-HA suite should continue to validate runtime integration separately rather than being used as the mutation runner.

Function Groups are not part of these campaigns yet. They can be added later if stable, high-value policy decisions are identified that benefit from mutation testing.

## Run locally

Mutmut requires an operating system with `fork` support. On Windows, run it through WSL.

With the normal test environment installed:

```bash
pip install mutmut==3.7.0
```

The checked-in `pyproject.toml` retains the `function-tools` campaign as the default local Mutmut configuration. Run it with:

```bash
rm -rf mutants
mutmut run
mutmut results
```

All four campaigns use the same runner locally and in Actions:

```bash
python scripts/run_mutation_campaign.py request-rules
python scripts/run_mutation_campaign.py guest-security
python scripts/run_mutation_campaign.py ha-permissions
python scripts/run_mutation_campaign.py function-tools
```

Run from the repository root. The runner temporarily replaces only `[tool.mutmut]` with the selected campaign's source module(s) and focused tests, then restores the original file even on failure. It clears generated `mutants/` state before each mutation run so switching campaigns cannot reuse stale results. The checked-in default remains the Function Tool configuration; there is no union of campaign modules or tests.

Use `--baseline-only` for pytest without mutation, or `--target 'MUTMUT_GLOB_OR_NAME'` to override default selectors within the selected module scope. Targets are passed as one literal CLI argument without shell expansion. Mutmut 3.7.0 selectors are checked against actual generated results. The runner prints selected counts and every survivor diff, rejects selectors that match nothing and incomplete results, and does not impose a mutation-score gate. Unselected `not checked` entries are not campaign survivors.

On Windows without WSL, use the existing manual Actions workflow for actual Mutmut execution:

```bash
gh workflow run mutation.yml --ref YOUR_BRANCH -f campaign=request-rules
```

The Request Rules selectors use verified generated `xǁRequestRulesǁmethod__mutmut_*` names; Python qualified method names do not select those mutants.

The workflow also accepts an optional Mutmut target override for narrower investigation within the selected campaign.

The `mutants/` directory is Mutmut's generated working state and can be removed to force a completely fresh campaign.

## CI

The `Targeted mutation testing` workflow is intentionally `workflow_dispatch` only. It is not part of normal pull-request CI and does not run a stable/dev Home Assistant matrix.

Run it from **Actions → Targeted mutation testing → Run workflow**, then choose one of:

- `function-tools`
- `guest-security`
- `ha-permissions`
- `request-rules`

Concurrency is grouped by branch/ref and campaign, so different campaigns can run independently.

The workflow:

1. installs Home Assistant, the repository test dependencies, and Mutmut 3.7.0;
2. temporarily configures Mutmut for the selected campaign;
3. runs that campaign's unmutated focused baseline tests;
4. clears stale Mutmut state;
5. runs only the selected mutation target(s); and
6. prints mutation results and surviving diffs.

An optional Mutmut target can override the campaign's default mutation target when investigating one function or method more narrowly.

## Reviewing survivors

Do not treat mutation score as a merge target. Review surviving mutants individually and classify them as one of:

1. a real missing behavioural assertion — add a normal pytest regression test;
2. an equivalent mutant — the mutation cannot change observable behaviour;
3. irrelevant/non-contract behaviour — for example wording that is intentionally not asserted exactly;
4. a mutation-tool artefact.

Only the first category requires a new test. Do not refactor production behaviour merely to make a mutation disappear, and keep any exclusions narrow and justified.

## Request Rules campaign review (2026-09-10)

The runs below record the initial review before rebasing onto PR #256. The current four-campaign runner configures only the selected campaign; the PR records validation runs on the rebased head. `tests/test_mutation_campaign_runner.py` checks isolation for all four campaigns, restoration after failure (including Windows line endings), selector/override validation and incomplete-result rejection.

The [first run](https://github.com/conorod1992/extended_openai_conversation/actions/runs/34426831096) killed 211 of 274 mutants, with 63 survivors. Reviewing every surviving diff led to 12 additional cases and stronger existing assertions for effective settings, custom wording groups, fuzzy candidate fall-through/position/ties, returned match metadata, sparse saved order, inactive-pattern fall-through, and sentence variants. These assert observable matching behaviour rather than cache internals or mocked scores.

The [second run](https://github.com/conorod1992/extended_openai_conversation/actions/runs/34427259763), on code commit `ea32639`, passed the 74-test campaign baseline and executed all 274 selected mutants: **242 killed, 32 survived**, with no timeouts, errors, or unchecked selected mutants. Every remaining survivor was inspected with `mutmut show`:

| Function / mutant suffix numbers | Classification and reason to leave them |
| --- | --- |
| `match`: 54, 85; `_sort_and_compile`: 46, 59 | Equivalent: changing the first argument to `typing.cast` has no runtime effect. |
| `match`: 5, 30; `_sort_and_compile`: 23, 66, 67, 89, 90 | Outside scope: aggregate sentence work budgets and compiled-state activation limits. These are meaningful resource-safety behaviours, not equivalent mutants; this matching/precedence campaign does not certify them. Existing bounds tests remain ordinary regression coverage. |
| `_sort_and_compile`: 9, 10, 14, 16, 17, 18, 19 | Outside scope: persistence dirty flags and canonical index bookkeeping. Relative winner order remains intact for the tested valid configurations; exact saved indices/save counts are not assertions of this campaign. These are not claimed globally equivalent. |
| `_sort_and_compile`: 52, 53 | Outside scope: rechecking mismatched capture names in malformed stored sentence variants. Public creation validates variant capture names separately; storage repair validation is not targeted. |
| `_sort_and_compile`: 54, 55, 56, 68, 69, 70, 71, 72, 76, 77, 81, 82 | Incidental/outside scope: diagnostic payloads and logging. Rule selection does not promise exact diagnostic content; management diagnostics are not certified here. |

Suffix numbers refer to `__mutmut_N` in the pinned version and source, not permanent exclusions. No pragmas suppress these mutants. All 23 `_deterministic_match` mutants were killed. A future change should re-review survivors rather than treating this list as an allowlist.

Local Windows validation also passed all 213 Request Rules tests and the original 85-test Function Tool baseline, using Python 3.14, HA 2026.8.0b3, and pytest-asyncio with the Linux-only HA pytest plugin disabled. Ruff lint/format and whitespace checks passed. A broader local run before the final 12 cases had 1,687 passed, 2 skipped and 16 unrelated failures: 15 reproduced on unchanged `develop`; an existing file-edit concurrency test failed in the working checkout but passed in the comparison checkout. Those failures were not fixed or hidden by this campaign. HACS validation passed; a normal GitHub `ci` matrix run was not reported for this draft PR. These limits should not be represented as a fully green integration suite.
