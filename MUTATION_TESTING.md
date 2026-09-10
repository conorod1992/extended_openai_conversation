# Targeted mutation testing

This repository uses Mutmut for deliberately small, manual mutation-testing campaigns around security- and correctness-sensitive decision boundaries.

## Campaigns

The manual workflow provides three selectable campaigns:

### `function-tools`

The existing default campaign. It mutates critical Function Tool execution logic in:

- `function_call_budget.py`
- `function_tool_resolution.py`
- `function_tool_recovery.py`
- `parallel_tool_execution.py`

It runs the existing focused Function Tool mutation-contract test set.

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

## Scope principles

Mutation testing here is selective rather than repository-wide. A campaign should target a small decision boundary only when its expected behaviour is clear enough to support reliable assertions.

Mutation testing complements the normal unit/property tests and real-Home-Assistant acceptance tests; it is not a replacement for either. The real-HA suite should continue to validate runtime integration separately rather than being used as the mutation runner.

Function Groups and Request Rules are not part of these campaigns yet. They can be added later if stable, high-value policy decisions are identified that benefit from mutation testing.

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

For the Guest Mode and HA permission campaigns, the GitHub Actions workflow temporarily selects the relevant source file and focused test file before invoking Mutmut. This keeps a normal local `mutmut run` backward-compatible with the existing Function Tool campaign.

The workflow also accepts an optional Mutmut target override for narrower investigation within the selected campaign.

The `mutants/` directory is Mutmut's generated working state and can be removed to force a completely fresh campaign.

## CI

The `Targeted mutation testing` workflow is intentionally `workflow_dispatch` only. It is not part of normal pull-request CI and does not run a stable/dev Home Assistant matrix.

Run it from **Actions → Targeted mutation testing → Run workflow**, then choose one of:

- `function-tools`
- `guest-security`
- `ha-permissions`

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
