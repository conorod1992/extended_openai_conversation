# Targeted mutation testing

This repository uses Mutmut for a deliberately small mutation-testing campaign around critical Function Tool execution logic.

## Scope

Only these production modules are mutated:

- `function_call_budget.py`
- `function_tool_resolution.py`
- `function_tool_recovery.py`
- `parallel_tool_execution.py`

The rest of the integration is intentionally outside the mutation scope. Mutation testing complements the normal unit/property tests and real-Home-Assistant acceptance tests; it is not a replacement for either.

## Run locally

Mutmut requires an operating system with `fork` support. On Windows, run it through WSL.

With the normal test environment installed:

```bash
pip install mutmut==3.7.0
rm -rf mutants
mutmut run
mutmut results
```

The `mutants/` directory is Mutmut's generated working state and can be removed to force a completely fresh campaign.

## CI

The `Targeted mutation testing` workflow is intentionally `workflow_dispatch` only. It is not part of normal pull-request CI and does not run a stable/dev Home Assistant matrix.

The workflow first runs an unmutated regression-test baseline for the targeted logic, then runs Mutmut using the scope in `pyproject.toml`, and finally prints the mutation results even when the campaign itself fails.

## Reviewing survivors

Do not treat mutation score as a merge target. Review surviving mutants individually and classify them as one of:

1. a real missing behavioural assertion — add a normal pytest regression test;
2. an equivalent mutant — the mutation cannot change observable behaviour;
3. irrelevant/non-contract behaviour — for example wording that is intentionally not asserted exactly;
4. a mutation-tool artefact.

Only the first category requires a new test. Do not refactor production behaviour merely to make a mutation disappear, and keep any exclusions narrow and justified.
