# Execution receipts: boundaries and maintenance

This documents the simplification of PR #1233 relative to
`bdbcefbadd7de24681f7069492a165fe8fe23271`. Replay protection belongs to a failed
provider continuation, scoped to its entity and conversation. An acknowledgment
ends that recovery interval; deliberately repeated actions within an ordinary
invocation remain separate effects.

## Changes

- The signature-to-schema recovery map also supplies membership and conversation
  order. The separate signature-set map duplicated those facts and was removed.
- One cleanup function now removes all six dispatch sidecars on acknowledgment
  or eviction. Rejected attempts receive ownership too, so acknowledgment can
  release their diagnostic records. Losing completed or uncertain history still
  retires the owner; losing authoritative refusals does not.
- Precise native receipts protect completed and uncertain operations by their
  prepared identities. A different prepared operation can proceed during
  recovery; the uncertain original cannot replay. Opaque backends, unknown
  service adapters, missing receipts and unidentifiable values remain
  conservative. Their outcome alone never establishes permission to replay.
- Genuine HA schema validation completes before the service receipt starts.
  Consequently, traceback and schema-frame provenance inspection was removed.
  A handler raising the same validation exception after an effect remains
  uncertain. Unknown adapters cannot claim pre-dispatch refusal.
- Public `async_services_for_domain()` replaces private service-table reads.
  The remaining private table assignment lives solely in `ha_service_compat.py`.
  HA exposes no public prepared-service dispatch API: a call-local registration
  preserves the original HA job while avoiding a second schema evaluation or
  a mutation of the global registry.

## Comparison

These measurements describe the initial extraction at `68add5cd`, before the
subsequent replay-hardening corrections documented below. They are a historical
comparison rather than current-head measurements.

Counts include the complete `execution_outcomes.py`, `tool_replay_guard.py`,
`ha_actions.py` and new `ha_service_compat.py`; the new boundary is not excluded
from the after count. Unchanged authorization code within `ha_actions.py` is
included on both sides. Documentation and tests are excluded.

| Measure | Before | After |
| --- | ---: | ---: |
| Physical production lines | 1,568 | 1,554 |
| Nonblank lines excluding comments | 1,368 | 1,348 |
| Static decision sites | 175 | 171 |
| Additional Boolean alternatives | 87 | 89 |
| Entity-owned mutable stores/flags in execution and recovery | 13 | 12 |
| Execution/replay ContextVars | 6 | 6 |
| Traceback/frame/code-object accesses | 6 | 0 |
| Private HA service-table accesses | 3 | 1 |

Decision sites are AST `if`, conditional expressions, loops, exception handlers
and matches. Boolean alternatives count each additional `and`/`or` operand.
These describe source control flow, not all possible runtime paths. The small
Boolean increase expresses the explicit precision/admission distinction.

The retained entity stores are six dispatch sidecars, three unresolved recovery
maps, and three retirement/saturation stores or flags. Replacing them wholesale
with a new record model would expand the change without eliminating their
different retention and ownership duties.

## Necessary complexity and residual risks

Prepared HA service identities lowercase domain and service names, matching native
dispatch. Entity services using string method names, and batched dispatchers which
strip selector fields, compare top-level selectors as sets and omit empty selector
lists. Callable entity handlers receive the full ServiceCall, so their selector
ordering and presence remain part of the identity. The recognized built-in
homeassistant power dispatcher compares entity IDs as a set because it forwards
sorted IDs, while preserving its other forwarded fields. Custom replacements for
those services remain opaque. Actual dispatch data, events, arbitrary payload
lists and action-batch ordering are unchanged. Exact whole-tool identities remain
conservative; prepared-effect matching supplies HA-specific equivalence.

Local Request Rule Functions and deferred jobs have explicit detached ownership.
Their invocation receipts still bound execution, but do not enter provider replay
history or evict its provenance. Unknown ownership retains fail-closed retirement.

Invocation leases, child paths, validated-call aliases and explicit settlement
handles remain: nested children, out-of-order completion and inherited task
contexts require different ownership checks. Detached jobs have a distinct
lifecycle from the invocation that admitted them. Generic persistence and
opaque operations still need broad refusal when their outcome is unknown.

Receipts retain compact signatures rather than prepared payload copies. Limits
remain 4,096 effects per invocation, 4,096 retained receipt records across
invocations, 4,096 dispatch/outcome entries and 32 detailed failed conversations.
Retirement evidence is bounded at 4,096 conversation digests. Saturation fails
closed; at most 32 acknowledgment exceptions permit subsequent work. These are
record limits, not a bound on the whole entity's memory: retained calls and
function schemas still obey their upstream input/configuration limits. This
change does not claim a universal byte ceiling or change those contracts.

The private call-local registry assignment remains a compatibility risk. Genuine
HA tests cover minimum, stable and upcoming versions: async/callback/executor
jobs, response modes, raw service events, context, one schema evaluation,
registration changes, handler exceptions after effects, equivalent and changed
retries, opaque adapters, acknowledgment and eviction. This is a small explicit
boundary to update if HA adds a public prepared-call API or changes registration
layout. Existing entity-service authorization inspection is outside this
receipt change and remains separately covered by its target-safety tests.

The new regression file fails three cases on the preceding head and passes on
the simplified implementation. Assertions check actual handler effects and HA
state, in addition to bookkeeping. Passing these tests does not prove every
custom service or adapter supplies a complete semantic operation identity.
