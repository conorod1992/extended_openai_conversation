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
dispatch. Recognized selector-stripping entity dispatchers compare the complete
resolved participant set, including availability, feature and permission filters.
Entity, area, device, floor and label selections that reach the same participants
therefore identify the same operation. Callable entity handlers retain their full
ServiceCall and observable selector ordering/presence. Actual dispatch data and
schema execution remain unchanged.

Recognized generic power dispatchers and string-method entity power services also
retain one bounded, hashed cross-family recovery reference per receipt. During a
failed continuation, an opposite-family retry with the same full target set,
power operation, user and response mode is conservatively refused. Their schemas
may supply different defaults, so this does not declare every service alias or
same-family payload equivalent. Different target sets and power operations remain
permitted; callable delegates are excluded from this cross-check.

Script service actions use the same prepared-dispatch receipt boundary as native
actions, preserving HA's actual response, blocking mode, context and schema events.
Their outer opaque receipt remains for Script behavior outside service dispatch.
A changed string/list/order/duplicate target representation cannot repeat an
unacknowledged service effect; genuinely different prepared effects still proceed
after a completed Script. Aborted Scripts retain their existing conservative outer
guard until acknowledgment, including for changed arguments. This does not equate
arbitrary Script arguments or replace their runtime values.

Generic power calls retain known delegate feature/device-class refusals, including
area selections that Core forwards as explicit delegate entity IDs. If every
delegate refuses, a corrected target is distinct. If another delegate may already
have acted, recovery uses the existing unidentifiable-effect guard until successful
acknowledgment. In that partial-refusal case, different target sets cannot establish
a safe new effect. An arbitrary handler exception never proves nothing executed.

Bash owns its receipt immediately before subprocess creation, after rendering and
validation. Its identity uses the rendered command, resolved working directory,
effective timeout, platform and the exact inherited environment supplied to the
subprocess. Only the hash is retained. Omitted versus explicit template defaults
cannot repeat an unresolved effect. Unidentifiable new effects are conservatively
refused during recovery. Acknowledgment still clears all recovery evidence and
permits ordinary repeated commands.

Local Request Rule Functions and deferred jobs have explicit detached ownership.
Their invocation receipts still bound execution, but do not enter provider replay
history or evict its provenance. Unknown ownership retains fail-closed retirement.
If independent work starts a nested provider exchange, that exchange's active
conversation scope takes precedence over the inherited detached flag. Dispatch
recording and receipt retention use the same resolved owner, so a later ordinary
provider retry remains protected after a failed nested continuation.

AI Tasks retain the same provider ownership as conversations. Their successful
provider continuation clears dispatched receipts before output parsing, while
failed or cancelled continuations retain replay evidence. A structured-output
validation failure after provider acknowledgement does not reopen that recovery
interval. Repeated successful tasks therefore cannot exhaust retention history.

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


REST Functions own receipts at the prepared HTTP dispatch boundary. Recovery
compares method, normalized destination and query, rendered headers (including
authentication identity), and payload, rather than the arguments that rendered
them. Omitted and explicit template defaults therefore cannot repeat an unresolved
completed HTTP effect. Opaque authentication uses conservative recovery protection.
Only digests are retained; a successful provider acknowledgement clears them and
permits intentional repeated requests. Transport failures keep uncertain receipts.

## Broadcast queue admission

Broadcast receipts identify the trimmed message, resolved recipient set and bounded
TTL. Native tools, HA services and Script Functions share that identity; route
labels, caller contexts and origin selectors remain delivery metadata. Origin
exclusion has already been applied to the resolved recipients. A failed provider
continuation therefore cannot repeat the same queued announcement through another
route. Successful acknowledgement still permits a deliberate later repetition.


## File mutations

File edits and writes record their prepared effect immediately before dispatch,
under the existing canonical-path lock. Identity includes the resolved path,
operation type and rendered content (or rendered old/new text for an edit).
Equivalent path spellings, template defaults and Composite Function wrappers
therefore cannot repeat an unacknowledged operation after a failed continuation.
Different prepared operations remain distinct; a successful provider
acknowledgment permits intentional repetition. Path authorization, atomic writes,
edit conflict detection and cancellation settlement remain enforced at the file
backend boundary.

## Transient Script cleanup

Script Functions and native Request Rules retain ownership until their one
stop/unload sequence settles, including repeated caller cancellation. A Rule's
already-started result-reference stop task belongs to that same sequence; it must
not become an interruptible await that skips unload. Native unload cannot simply
be retried because HA marks the runner unloaded before awaiting its final stop.

The cleanup task inherits its caller's context. Parent cancellation is deferred
using `asyncio.wait`, then the owned result is observed explicitly. Directly
shielding a failing task can invoke HA's shield-error logger after cancellation,
exposing private cleanup exception text even when the caller later observes it.
An active caller cancellation is preserved with generic cleanup diagnostics;
ordinary cleanup failures still propagate. Older HA's `async_stop` fallback keeps
the same settlement behavior. Action effects, contexts, receipts and successful
provider acknowledgement boundaries are unchanged.

An existing cancellation retains its original identity. Cancellation first
received during cleanup propagates after settlement. Ordinary cleanup failures
still propagate, while failures secondary to cancellation use a generic local
diagnostic. An earlier cancellation already handled by the caller is not treated
as active merely because `Task.cancelling()` remains nonzero. The execution and
receipt scope remains alive until cleanup settles.
