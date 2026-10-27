# Context measurements and provider usage

Context management and usage accounting have separate representations for each
provider round. `RequestTokenMeasurements` owns a provider-only `RequestUsage`
snapshot in `reported` and a separate `context_estimate`. Only `reported` crosses
the accounting boundary. Estimates never occupy its token fields or details,
and `UsageManager` does not interpret context metadata or strip private markers.

## Provider snapshots

Each accepted observation merges explicitly supplied cumulative provider fields
into the previous snapshot. Repeated observations are not added together.
Absent fields and SDK defaults do not erase known counts. This applies to
ordinary, trailing and terminal observations from both APIs and completed-response
adapters.

Missing and all-zero headline-only reports leave the existing snapshot intact.
An otherwise usable report can explicitly correct a field to zero, including
input tokens. Valid explicit detail observations are usable independently;
zero detail corrections clear the earlier detail count. Provider-specific positive
details, cached tokens and reasoning tokens retain their existing interpretation.

Positive explicit provider totals remain authoritative. When they are absent,
totals are derived from the known provider components only under the existing
completeness rules; an unexplained provider total is retained until those rules
allow replacement. Local estimates never participate in that calculation.

With no usable report, accounting retains zero provider tokens and still records
the request's existing success/failure and duration metadata. Stored aggregate,
daily, run and request formats, historical records, pricing interpretation and
the token-only management UI are unchanged. No storage migration is needed.

## Context selection

Before dispatch, the request's existing prepared-input estimate supplies the
context fallback. Output-, total- and detail-only reports leave that fallback
active. The first accepted positive provider input permanently retires it for
that round. A later explicit correction to zero input then uses the known provider
total, rather than resurrecting the earlier estimate.

After a successful round, the selected count is bound to the prepared prefix in
the immutable `ContextInputMeasurement`. Its history length and input revision
continue to reject rewritten or shortened prefixes while allowing appended output.
Preflight trimming, summary-before-pruning order, remeasurement, model limits,
attachment budgeting and formatted-tool caches retain their existing policies.

## Observation and lifecycle ownership

The same round-local record flows through completed-response adaptation,
stream normalization, parsing and bounded trailing recovery. Capture happens
before output interpretation, so malformed output cannot discard an already
observed provider report. Capture publishes a new independent provider snapshot;
previously retained snapshots and their details do not change.

Diagnostics and ChatLog stats still describe raw normalized provider observations,
not context estimates or the merged accounting snapshot. A zero diagnostic event
can therefore coexist with earlier retained accounting counts.

Success and failure paths each record the current provider snapshot once. A new
provider round starts a fresh record, including after tool execution or recovery.
Nested requests and usage-run ownership keep their existing boundaries. Trailing
recovery deadlines, event limits, cancellation, stream closure and late-result
rejection remain unchanged.

## Verification

Focused tests independently check context selection and provider accounting for
missing, explicit zero, partial, repeated and corrected reports, including SDK
field-presence rules. They also check that estimates cannot enter persisted
totals, runs or request details. Genuine HA acceptance exercises SDK transport,
trimming and immutable prefix measurements, native storage, parser failures and
trailing usage using the same behavioral oracle against baseline and candidate.
