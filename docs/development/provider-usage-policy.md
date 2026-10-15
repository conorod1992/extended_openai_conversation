# Streamed provider usage policy

This follow-up adopts two deliberate changes to the behavior characterized in
PR #1245. That PR remains unchanged and records the historical baseline.
The follow-up carries forward the same characterization test files with deliberate
policy updates, rather than introducing a second suite of conflicting assertions.
If #1245 is merged first, retain the follow-up expectations when rebasing this PR.

## Usage snapshots

Missing and all-zero usage reports do not retract an earlier usable report from
the same provider request. A usable report contains a nonzero input, output,
total, cached-input or reasoning count. Each new usable report replaces the
previous provider snapshot as a whole; reports are neither accumulated nor merged
field by field. This applies to ordinary, trailing and terminal events alike.

When no usable report arrives, accounting records zero provider tokens. The
request's local input estimate remains available for context management. EOAI
does not interpret a zero terminal snapshot as an authoritative correction; a
provider-specific correction contract would require a separately reviewed change.

## Context fallback and accounting

An active local input estimate survives missing input counts whether the stream
completes, fails or is incomplete. A usable provider input count replaces and
retires that fallback, as before. Later partial snapshots are still replacements;
this change does not resurrect retired estimates or merge earlier provider counts.

`usage_for_accounting()` continues to remove local estimates before persisting
Usage aggregates and details, while preserving genuine partial counts. ChatLog
stats and provider diagnostics report raw provider values, never local estimates.
Thus a zero diagnostic event can coexist with retained earlier accounting usage.

The request loop observes context usage on its success path. Consistent fallback
on incomplete errors is not itself evidence of a billing or public context-trimming
defect in the old implementation.

## One application owner

The Chat and Responses adapters apply usage through `_capture_provider_usage()`
before yielding each event. The parsers no longer assign usage, and neither
adapter repairs usage after yielding. `_restore_local_estimate()` and its saved
estimate override are removed. This reduces the two production files by 54 lines
without adding a usage accumulator, event-state framework or shared stream parser.

Terminal stats remain after parser admission, including the initial Chat role
yield and Responses terminal-sequence validation. Their read-only normalization
does not apply usage. `_trace_provider_usage()` shares the stats shape while
keeping diagnostic admission and timing unchanged. Provider debug summaries still
describe the raw wire events; they are independent from the retained accounting
snapshot.

## Scope and validation

Chat length finishes still drain trailing usage before raising. Responses
incomplete events still raise at the terminal event. Stream closure, cancellation,
tool handling and auxiliary non-streamed requests retain their existing policies.

Focused tests check all token categories, repeated snapshots, successful/failed/
incomplete terminals, interruption and suspended-generator closure. Genuine HA
tests exercise public processing, actual SDK SSE parsing, native Store snapshots,
in-flight context values and provider diagnostics. Historical zero-retraction and
incomplete-fallback assertions are deliberately replaced by the policies above;
other characterization expectations remain intact.
