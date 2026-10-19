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

A final review pass additionally reproduces three failures where hydrated runtime
Tool configs caused Group-only repair to reserialize unchanged Functions YAML. The
comparison now uses the existing persistence-safe deepcopy contract, retaining raw
comments and formatting. All three targeted cases pass after that correction.

The first full stable-HA run of this follow-up (`ba1f6b0`) reached 9,426 passes,
6 skips and 98.49% coverage, with one exhaustive seam test still mocking the old
Tool-only entry check. That test now supplies the shared repair state instead;
its assertions that absent Function fields remain absent are unchanged, and it
also asserts one shared-state lookup and one save. No production change or test
suppression is needed for this CI correction.

## Group identity, hidden membership and native UI follow-up

The review of `aa3906b` identified rename collisions with quarantined IDs,
incidental membership edits losing unresolved references, Group-only damage routing
into the Tool fallback editor, and `validate_current` reporting only the usable
projection as if it certified all stored definitions.

Group persistence now distinguishes raw definitions, their usable baseline and the
explicit mutation target. All persisted string IDs remain reserved during creation
and rename. Ambiguous duplicate IDs cannot be edited/deleted by an ID-only command,
and unrelated Tool changes retain both duplicate raw entries. Incidental Group
membership updates apply only the intended usable-member delta, preserving missing
Tool references, malformed members and untouched raw fields. Tool rename/delete
semantics also apply to structurally quarantined Groups. Explicit Group repair or
deletion consumes only its selected definition. Strict proposed-Group validation,
revision checks, dependency handling and deletion confirmation remain in force.

The response identifies valid Tools independently from Group damage. The frontend
keeps normal Functions controls and exposes uniquely identifiable malformed Groups
through the existing Group editor/delete controls, including malformed or empty
string IDs. Repair preserves disabled and Guest settings and safely initializes
malformed fields. Mutation acknowledgements refresh repair metadata immediately.
The saved-definition validation button now reports excluded Tools/Groups as needing
repair; healthy definitions still produce the existing success result.

The same final 48 public-command cases in
`tests/test_group_mutation_preservation.py` yield **36 failures and 12 control passes**
on untouched reviewed head `aa3906b`, and **48 passes** with the correction. The
five native Chromium cases in `tests_browser/group-quarantine-repair.spec.mjs` fail
with the reviewed frontend and pass after the fix. They exercise real DOM routing,
edit/save/delete dialogs, acknowledgement refresh and validation messages against a
controlled transport fixture; actual HA-command behavior is tested separately by the
public backend cases. These are not claims of a new live-HA corruption reproduction.

The expanded related backend run has **2,783 passed, 1 skipped and only the ten
previously reproduced Windows/platform failures**. Existing assertion coverage is
retained; the persistence response assertion additionally verifies updated repair
metadata, and the frontend composition assertion still requires repair controls to
precede normal Groups. Pinned Ruff/MyPy pass all 146 source files. Required frontend
syntax, standalone, type/unit and deterministic double-build checks are run, and
tracked generated assets are rebuilt from source.

Group mutation intent is explicit: ordinary edits, availability toggles and
assignment changes preserve hidden members in both source and destination Groups.
Only the dedicated repair controls submit `repair: true` to replace the targeted
Group's unavailable members; deletion remains separately confirmed. Additional
public-command cases cover that distinction, and the native browser transport
asserts that the repair form actually sends the explicit intent.

Final native-browser validation passes all **89 related source-mode journeys** and
all **five Group-repair journeys against the rebuilt shipped bundle**. The same five
recovery journeys fail against the reviewed frontend. All 106 standalone frontend
files, frontend typing, 39 Vitest tests and the deterministic double build pass.

The first full stable run of this pass had 9,474 passes and one inventory-tripwire
failure: adding the Group Edit/Delete controls increases the repair module's
interactive markers from five to seven. The reviewed inventory now records those
two controls and their conditional recovery scenario. The existing exact source
marker assertions remain intact, and the native recovery campaign runs all five
scenarios across all five reviewed viewport classes (25 cases). A registration
tripwire requires the conditional controls, save/delete actions and viewport
campaign to remain linked. No production changes are needed for this correction.

## Assignment transitions and reserved Group names

The review of `fcf0707` reproduced two further preservation/validation gaps through
public management commands. Existing usable assignments were incorrectly treated
as instructions to strip duplicate references from quarantined siblings. Changed
Group names were checked only against usable Groups, allowing a collision with a
quarantined sibling's saved name.

Quarantine persistence now compares the usable assignments before and after the
operation. Tool and Group renames preserve identity during that comparison.
Metadata edits and availability toggles leave duplicate raw references untouched;
intentional moves and Tool removals/renames still update affected memberships.
Removing a conflicting assignment or Group preserves the other Group's reference.
Acknowledgements project the actual resulting configuration before committing, so
Groups made usable by an intentional change appear immediately and match a fresh
configuration read.

Group creation and name changes reserve the trimmed, case-folded names of every
other persisted Group, including Groups missing an ID or required description.
An unchanged normalized name does not block an unrelated edit merely because a
collision already existed. Rejected collisions preserve all configuration and
Guest references and perform no update.

The same final **25 public-command cases** in
`tests/test_group_assignment_transitions.py` and
`tests/test_group_name_transitions.py` produce **21 failures and four control passes**
on untouched reviewed head `fcf0707`, and all pass after the correction. Coverage
includes metadata/availability/ID changes, intentional Tool moves/renames/deletions,
Group unassignment/deletion/explicit repair, mutation responses and subsequent
reads, new/renamed case-insensitive collisions and pre-existing collisions.
The focused public/configuration/validation-reuse selection passes **243 tests**.

Two existing private-persistence assertions expected the submitted Group list to
be echoed rather than the canonical saved projection. They now require canonical
defaults and exclusion of a structurally unusable Group respectively. All their
raw Tool/Group preservation, revision, and repair-metadata assertions remain in
place. Pinned Ruff checks/format and MyPy pass all 146 source files. There are no
frontend source or generated-asset changes in this pass.

The expanded 135-module local run has **2,808 passed, one skipped and only the ten
previously reproduced Windows/platform failures** (seven Linux staging-lock paths
and three bash/process-group paths). No skips or platform accommodations were
added; full Linux CI remains required.

## Final review of `9875a8e`

The final source/caller review covered the original nine changes, Group editor
routing, raw/usable/persisted projections, configuration and recovery writers,
rename/delete dependencies, failure responses and revision guards. Three bounded
areas needed further correction:

- Normal Tool rename rollback rebuilt configuration from usable Tools/Groups,
  which could lose hidden references and raw YAML. It now restores the exact raw
  snapshot only while the saved revision remains current. Both normal and dedicated
  repair rename handlers also respect settled Request Rule storage: a lost save
  acknowledgement after commit retains the renamed Tool/Groups, while a failure or
  cancellation before commit restores the original configuration. A committed
  rename followed by an acknowledgement error explicitly tells the user it was
  saved and to reload. Concurrent configuration edits remain authoritative.
- Dedicated repair `configuration_save` rechecks the operation's revision after
  asynchronous validation, including when its legacy caller omitted a revision.
  It cannot overwrite a newer prompt or Group edit made during that await.
- Group creation counts all raw saved entries against the shared existing limit
  of 50. Quarantined definitions retain their slots. Metadata edits and targeted
  repair remain possible at the limit, and creation below the limit still works.

The same final **34 regression/control cases** in
`tests/test_group_rename_rollback.py`,
`tests/test_repair_configuration_save_concurrency.py`,
`tests/test_group_quarantine_capacity.py` and
`tests/test_function_rename_rule_settlement.py` produce **17 failures and 17 control
passes** on untouched reviewed head `9875a8e`. The settlement cases use the actual
production RequestRules manager and save/reconciliation code with a fault-injected
in-memory Store; they assert both current state and a fresh manager read. They do
not claim a live-HA filesystem failure reproduction.

The existing mocked rollback test now requires one validated save followed by an
exact raw restore, rather than a second projected save. It retains the original
exception assertion and additionally checks the exact restore payload and Rule
rename arguments. No existing regression is skipped or relaxed. Pinned Ruff
checks/format and MyPy pass all 146 source files. No frontend rebuild is needed for
these backend-only corrections.

All **34 final cases pass** with the corrections. The expanded **139-module** run
has **2,842 passed, one skipped and only the same ten previously reproduced
Windows/platform failures**. Full Linux stable/dev and selected browser, lifecycle,
recovery and upgrade CI remain the final acceptance checks.

The first full stable run reached **9,532 passes, six skips and 98.45% coverage**,
with three existing repair-rollback tests failing because their reference lookup
returns an empty metadata mapping. Missing reference metadata now conservatively
means a durable Rule rename cannot be certified. The two readers use an empty
reference-list default; the existing rollback/revision tests remain unchanged.
All **48 tests** in that existing module and the four new regression files pass,
including the three CI cases.

Including that entire unchanged module, the final **140-module** local run has
**2,856 passed, one skipped and only the same ten Windows/platform failures**.

## Further review: unreadable Rule save recovery

Review of `0dda997` found one remaining distinction in rename recovery. When both
a Rule write and its authoritative readback fail, RequestRules invalidates its
execution state and clears references. The rename helper treated that empty view
as non-commit evidence. Both management routes could then undo a saved Tool/Group
rename even when the Rule rename had actually reached storage.

The shared helper now distinguishes confirmed commit, confirmed available-state
rollback and unknown storage outcome. Unknown outcomes retain the already-saved
Tool/Group configuration and leave Rule execution invalidated. The management
error explicitly says the Rule state cannot be verified and asks the user to
restore storage access and review references. Cancellation still propagates.
An authoritative read is needed to establish whether the Rule write committed;
automatic rollback cannot be inferred from the cleared, invalidated view.

The same final eight public-command regressions in
`tests/test_function_rename_unknown_outcome.py`, combined with the eight existing
known-outcome controls, yield **eight failures and eight passes** on untouched
reviewed head `0dda997`. They cover both normal and dedicated repair routes,
pre/post-commit failures, cancellation, retained raw configuration, one accepted
configuration write, fail-closed Rule state and a fresh read after storage becomes
available. The tests use the production manager with a fault-injected in-memory
Store; they do not claim live-HA filesystem fault injection.

The focused recovery/concurrency selection passes **52 tests**. Pinned Ruff
checks/format and MyPy pass all 146 source files. No existing test assertions,
platform policy or frontend assets change in this pass.

The expanded **141-module** run has **2,864 passed, one skipped and only the same
ten previously reproduced Windows/platform failures**. All 16 selected
unknown/known-outcome cases pass with the correction.
