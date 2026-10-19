# Nine state, configuration and context regressions

Base revision: `3ec674803f6e5e80d19cb604d5f896b10c8bfaa6`.

The implementation uses nine separate fix commits, followed by a bounded review
pass. It reuses the existing request-preparation and configuration-projection
mechanisms rather than introducing replacement frameworks.

## Scope and related paths

| Finding | Correction and related paths checked |
| --- | --- |
| 1. Quiet Hours ownership | Reconcile observed state before the unchanged-target/ceiling-correction exits. Check switches, volume rounding, retry exhaustion, pending feedback, retargeting, unavailable controls and persisted recovery. Prepared intents retain their separate baseline/context checks. |
| 2. API-aware budgeting | Remove the initial Responses-only destructive estimate. The existing per-round boundary budgets the selected API's prepared messages, attachments and live tools, then dispatches that representation. Check Chat/Responses conversion, tool results and preflight summaries. |
| 3. Measurement revisions | Keep the latest request-local input measurement and fingerprint its prepared history prefix. Rewritten/reduced prefixes require a fresh estimate; appended output cannot revive an earlier round's maximum. Usage accounting remains independent. Check tool rounds, native records, in-place edits and attachment changes. |
| 4. Memory compatibility | Use the same opaque, nonempty scope-ID contract for live writes and stored records. Session IDs are not owner IDs and have no 128-character owner-ID limit. Existing long identifiers round-trip unchanged through restart, export, validation and replacement. Owner-ID, record-count, content and backup transport limits remain enforced. Inspect sibling Memory/Knowledge and backup validation/replacement boundaries; do not introduce a global schema migration. |
| 5. Reasoning inheritance | Duplication reuses the existing model-setting omission projection. Check explicit `none`/`high`, omitted settings, configuration transfer and recovery projections, model changes and catalogue-derived defaults. Current duplication materialized the recommended profile, not universally `none`; the corrected contract preserves inheritance. |
| 6. Sentence grammar | Separators can disappear at the existing logical sentence boundary. Check nested trailing optionals, normalized Unicode punctuation, required internal separators and existing capture/alternative tests. |
| 7. Speech formatting | Use the streaming sanitizer for completed responses too. Handle nested single/strong/triple delimiters, combined closing delimiters and literal spaced exponentiation. Check every split of representative strings, character-at-a-time input, links, code, escapes, Unicode, malformed/bounded buffers and custom replacements. |
| 8. Broadcast lifecycle | Queue TTL governs admission/waiting. A bounded message-aware deadline covers the blocking Home Assistant synthesis/playback call. Check expiry during delivery, completion, stalled cancellation, busy queues, permissions and existing disable behavior. The HA `AssistSatelliteEntity.async_internal_announce` contract awaits `async_announce` until playback completes. |
| 9. Quarantined configuration | Field-only saves obtain the repair projection independently of management section. Invalid Groups are excluded from the validation/display candidate while raw Functions/Groups remain unchanged. Check Knowledge, Archive, prompt, model, Skills and Memory settings, group-only failures, mixed tools, strict changed-field validation and rejection of Function/Group writes through the field-only helper. |

## Before/after proof

Copy only the updated regression test files into a detached checkout of the base;
leave its implementation files untouched. Run the same selection in both checkouts:

```sh
python -m pytest \
  tests/test_request_rule_patterns.py \
  tests/test_configuration_operation_contracts.py \
  tests/test_management_function_quarantine.py \
  tests/test_temporary_memory.py \
  tests/test_quiet_hours_manual_ownership.py \
  tests/test_intercom.py \
  tests/test_speech.py \
  tests/test_request_budget_preparation.py \
  tests/test_request_budget_revisions.py \
  -k 'logical_sentence_end or duplicate_preserves or field_only_updates or long_opaque or unchanged_target_releases or outlives_old_timeout or nested_formatting or chat_budget_omits or final_maintenance_does_not' \
  -q --junitxml=regressions.xml
```

| Revision | Result |
| --- | --- |
| Original implementation | **43 failed, 3 passed**, no collection/setup errors |
| Fixed implementation | **46 passed** |

The three original passes are explicit-setting/speech controls. Every finding has
a failing behavioral regression on the original implementation. Broadcast tests
scale deadline seconds to milliseconds while executing the real drain operation.

Two existing expectations deliberately change: duplication must retain reasoning
omission, and a 129-character session scope is accepted rather than rejected.
The latter rejection case is replaced with invalid-type coverage and explicit
long-scope round-trip tests; other validation assertions remain intact.

## Wider local validation

An 88-module related-path run produced **1,943 passed, 1 skipped and 7 failed**.
The same seven failures reproduce on the original implementation: Windows lacks
`fcntl`, required by transfer staging. The full-suite attempt likewise cannot
collect `test_transfer_staging.py`; a subsequent wider attempt hit a Hypothesis
source-discovery timeout. These are not recorded as passing Linux validation.

On Windows, unit tests were run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and explicit
`-p pytest_asyncio.plugin` (plus `-p pytest_timeout --timeout=30` for the related
run). This avoids the HA test plugin importing its Linux-only runner. Tests use
an ordinary permitted Windows event loop and a workspace `--basetemp`; neither
production code nor fixtures are patched to simulate Linux locks.

Additional review cases live in `tests/test_nine_contract_review.py`.

Pinned Ruff **0.16.8** lint and format checks pass. Pinned MyPy **2.3.1** with
`--platform linux` reports **no issues in all 146 source files**. This checks Linux
typing contracts locally; it does not replace runtime validation on Linux CI.

## Group-repair follow-up review

The review of head `cd36684` found Group-only gaps in normal Configuration Save,
legacy Update and backup export, plus a crash on object/list Group members. The
same public-route selection (`group_only or group_repair_records` in
`test_group_quarantine_public_contracts.py`) produces **28 failures and 2 control
passes** on that head. It covers Get → Save, cold Save, Update, Validate, Knowledge,
public backup creation and the three recovery/configuration projections.

The shared field-only decision now applies to these writers. A recognizable Tool
collection with quarantined items, or invalid Groups with valid Tools, can remain
untouched while unrelated fields are validated and projected. Object/list members
are recorded as Group repair issues before membership lookup. Functions YAML that
cannot decode to a collection (or a collection-wide invariant failure that cannot
be isolated) still requires explicit repair before an ordinary field-only save.
Submitting Functions or Groups always invokes strict edit validation. Private
recovery retains its existing ability to snapshot opaque malformed input.

The older scoped-persistence tests now assert exact raw preservation and successful
unrelated edits instead of rejection on selected UI routes. Both malformed-
collection rejection assertions in `test_save_validation_reuse.py` remain intact.
The normalization-count tests observe the shared normalizer directly and continue
to require exactly one normalization, including failed writes. Additional tests
cover explicit strict edits and legacy default-Tool semantics.

## Group editor, repair responses and diagnostic follow-up

Review of `4f43fce` identified three sibling gaps: Group editor mutations validated
malformed saved Groups before reaching their replacement/deletion; dedicated Tool
repair responses still used a Tool-only projection after committing; and Guest Mode
and provider diagnostics did not consistently recognize Group-only damage.

`function_configuration_repair_state()` now supplies the legacy repair payload and
read-only management Group projection too. When a malformed collection contains
usable Group siblings, those siblings remain available. Group mutations locate the
explicit target in raw persisted definitions, strictly validate the proposed usable
result, and consume only the targeted raw definition. Untouched malformed and healthy
siblings retain their raw fields; Group-only mutations also retain the original
Functions representation. Creating a Group cannot overwrite a quarantined identifier
without explicitly selecting that existing Group. Tool edits preserve even an opaque
non-collection Group field. Bulk Tool repair uses the same boundary as single-item
repair, and diagnostics report Group degradation alongside the usable projection.

Public-command regressions are in `tests/test_group_repair_command_contracts.py`.
They cover Group replacement, deletion and creation with both Group-only and mixed
Tool/Group damage, single-item and bulk Tool repair, post-commit response construction,
subsequent configuration reads, provider diagnostics and Guest Mode details. Controls
retain invalid-proposal rejection, stale revisions, missing targets, explicit deletion
confirmation, raw sibling preservation and identifier collision protection.

The **same final 66 tests** produce **36 failures and 30 control passes** on untouched
reviewed head `4f43fce`, and **66 passes** with the correction. There are no collection
or setup failures in that replay. Existing seam tests now observe the shared repair
state, use valid Group fixtures for Tool-only cases, and retain their persistence and
dependency assertions; an old assertion accepting a numeric Group member now requires
that malformed member to be excluded from the diagnostic projection.

The expanded 131-module non-property local run produced **2,726 passed, 1 skipped and
12 failures**. Ten failures reproduce on `4f43fce` (Windows lacks `fcntl`/`killpg`, plus
the existing defensive-path case). The other two file-edit failures pass when repeated
on both heads; the complete file-edit module passes all 42 cases on the corrected head.
A broader attempt including property tests hit Hypothesis source-discovery timeout on
Windows. These local runs are not claimed as a passing full Linux suite. Pinned Ruff
and MyPy still pass, with all 146 source files checked; the full Linux stable/dev CI
remains the final validation gate.
