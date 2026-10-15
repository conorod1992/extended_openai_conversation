# Provider stream usage: bounded consolidation assessment

This records observations of the unchanged implementation, not approval of every
observed behavior as a permanent accounting contract. No production code changes
are included in this investigation.

## Boundaries that must remain separate

- The in-flight `RequestUsage` may include a local input estimate for context
  management. Its total can still be the provider's partial total; the fields are
  not necessarily additive.
- `usage_for_accounting()` removes the local estimate before Usage persistence,
  retaining genuine partial output, total, cached-input and reasoning counts.
- ChatLog stats and provider diagnostics report provider values, not the estimate.
- Chat and Responses have different terminal and trailing-event behavior. Chat
  drains trailing usage after a length finish before raising; Responses raises
  during the incomplete event. Auxiliary non-streamed requests are outside this
  proposed extraction.

## Baseline findings requiring explicit policy decisions

| Event | Existing result |
| --- | --- |
| Missing or all-zero report with only a local estimate, successful completion | Keep estimate for context; persist zero provider tokens |
| Partial report with no input count, successful completion | Keep estimated input for context; persist only provider counts |
| Full report | Replace estimate with provider snapshot |
| Repeated nonzero reports | Replace snapshot; do not add counts |
| Missing terminal report after genuine usage | Keep previous genuine snapshot |
| All-zero terminal report after genuine usage | Clear previous genuine snapshot, including persisted token counts |
| All-zero nonterminal report after genuine usage | Keep previous genuine snapshot |
| Partial/zero Responses incomplete report | Parser overwrites the estimate; raising prevents wrapper repair |
| Partial/zero Responses failed report | Wrapper retains estimate; failed-event parser does not overwrite it |
| Partial report followed by disconnect, cancellation or consumer closure | Preserve partial provider counts and local context estimate |

Two decisions block a behavior-preserving simplification:

1. **Does an all-zero report retract a previous report?** Today the answer depends
   on whether the event is terminal. Ignoring zero reports universally changes
   persisted accounting; accepting them universally changes nonterminal behavior.
   This investigation does not choose either interpretation.
2. **Should local context fallback survive Responses incomplete errors?** Today
   partial/zero incomplete events discard it while completed and failed events
   preserve it. Accounting remains provider-only in these probes. The public
   request loop observes context usage on its success path, so this alone does
   not establish a user-visible truncation or billing defect.

The tests explicitly preserve these asymmetries for review. A later intentional
policy fix should update the corresponding assertions, rather than treat the
characterization as evidence that the asymmetry is desirable.

## Attempt and stopping point

The first 46 focused cases passed before editing production code. A temporary
experiment removed the two parser assignment blocks and both post-yield repair
blocks, retaining existing pre-yield capture and diagnostic traces. Seven cases
changed: five partial/zero Responses length cases and two zero-terminal-after-full
cases. The production files were restored byte-for-byte after the experiment.

Preserving those differences in one writer would require additional terminal and
error-specific overwrite/repair rules. That moves the policy coupling rather than
clearly reducing it. The bounded attempt therefore stops at characterization and
defers consolidation until the two policy decisions above are resolved. It does
not introduce a new usage accumulator or shared Chat/Responses parser.

## Evidence and ongoing coverage

`tests/test_provider_usage_characterization.py` checks context state, the
accounting projection and exact ChatLog stats for missing, zero, full, input-only,
output-only, total-only, reasoning-only and cached-only reports; repeated reports;
successful/length/failed terminals; disconnect; cancellation; and consumer closure
while the wrapper is suspended. Expectations are explicit snapshots, not generated
by the production normalizer.

`tests_real_ha/test_provider_usage_characterization.py` uses genuine HA public
conversation processing, the actual OpenAI SDK's SSE parser, native HA Stores and
enabled provider diagnostics. It verifies successful and length-error reports in
both modes, the in-flight values passed to accounting, exact persisted aggregate
and detail counts, and missing-versus-zero reports following genuine usage.
The HTTP replies are deterministic local fixtures; no live provider is billed.

The new cases are assigned to the enhanced-nightly provider-resilience campaign
and its reviewed execution catalog; ordinary Python and genuine-HA CI discovery
also includes them. Existing trailing-usage and failure/recovery tests remain
unchanged.
