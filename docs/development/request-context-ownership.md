# Request context ownership

Request state has several lifetimes. Keep the owner and its reset boundary
explicit when adding a consumer. A copied asynchronous context inherits bindings;
it does not acquire a new conversation, user, execution receipt or storage lease.

## Owned bindings

| Owner | State | Lifetime and inheritance |
| --- | --- | --- |
| Public invocation | `RequestInvocation` in `request_context.py` | Each `async_process` or `async_process_direct` call installs fresh metadata and prefetch ownership. Ordinary child helpers inherit the binding; another public invocation installs its own. |
| Entity registration | Existing readiness event in `conversation.py` | Each HA add captures its own event. Removal retires that registration before initialization can resume; completion signals only its captured event. Requests reject a changed registration after waiting. |
| Claimed turn | `ConversationTurnContext` in `request_context.py` | One resolved data scope, archive identity, privacy veto, temporary-memory scope, memory-session identity and Function Group session reference. Installed atomically after session lookup; restored even if admission or finalization fails. |
| Native Request Rule script | `RuleActionExecution` in `request_rules.py` | The function callback, action authorizer and captured-result map are installed and restored together. Native script branches intentionally share that result map. A nested rule installs another record. |
| Prepared Function call | `PreparedFunctionCall` in `function_execution.py` | The exact prepared input, validation snapshots and retained original input share one dispatch boundary. Nested dispatch restores its caller's binding. |

These records are frozen bindings, not recursively frozen copies. Metadata and
captured results retain their existing dictionary sharing. A Function Group
session remains mutable state owned by its logical-conversation runtime. The
records do not copy configuration, histories or Home Assistant objects.

Archive changes replace the **current task's** turn record. The privacy tool sets
the turn veto before awaiting storage. Resuming saving changes the archive identity
without clearing that veto, including a veto set during the awaited operation.
Mutating a shared record, or replacing a record captured before the await, would
break this contract.

Public invocation cleanup cancels and drains only an abandoned prefetch owned by
that invocation. A nested request cannot consume or cancel its parent's prefetch.
The ordinary successful path consumes the prefetch once and preserves overlap
with persistent-memory retrieval. Cleanup preserves the primary request error or
cancellation, retrieves speculative-read failures without logging their private
prose, and drains through repeated caller cancellation. Completion notifications
retain their existing ordering inside the invocation boundary.

Registration retirement is bound before the first initialization await. Stage
boundaries prevent an obsolete add from publishing a retention interval or legacy
alias. An older add's failure cannot mark a newer registration failed. This also
protects requests waiting through a rename, reload or same-object re-add: waking
an old readiness event does not grant access to a later registration. Already
running initialization helpers retain their existing storage and settlement
contracts; these checks own publication and request admission. Removal waits for
pending initialization to settle before finishing HA teardown, so HA's final add
state write cannot happen after removal. Already-ready registrations keep the
ordinary removal path without an additional task or wait.

EOAI-owned reloads settle the entire Core transition and their reconciliation
before propagating caller cancellation. Restore reloads clear the pending marker
only after success; configuration listeners finish removed-subentry cleanup.
The existing settlement helper preserves an underlying failure over caller
cancellation. Live configuration updates and restores without a pending reload
retain their direct paths without a settlement task.

The reload task is created through HA's tracked, eagerly started task API before
the settlement helper awaits it. A plain untracked task changes HA's existing
reload ordering and can let its drain boundary miss replacement initialization.
The task belongs to HA rather than the entry being unloaded; entry-owned tasks
would be cancelled by that same reload. The helper awaits the existing task and
does not create a second one.

Full entry unload captures its own platform objects before Core removes them from
the registry. Only after successful unload does it idempotently stop those old
objects' polling. This covers Core's empty-platform reset path, which returns
before stopping polling. Failed unloads retain active polling, and other entries
and replacement platform generations are excluded by owner identity. No private
timer fields, global patches or request-path work are introduced.

Core owns the outer state transition when another caller directly invokes its
config-entry unload API. Cancelling that operation can leave the entry in
`UNLOAD_IN_PROGRESS`, even after the entity's initialization and cleanup settle.
The same entry then cannot be set up again until Core recovers or restarts. The
entity hook cannot complete an outer transition it does not own. This remains a
limitation for independently cancelled Core unload calls, rather than EOAI-owned
restore or configuration-listener reloads.

## Identity and configuration boundaries

Authenticated Home Assistant context remains the authority for native actions.
Voice Identity resolves data ownership and physical origin independently. The LLM
context is created after the Voice Identity scope has entered; moving it earlier
can capture the wrong request identity.
Its binding is restored even when initial Guest policy resolution fails before
the turn is claimed. Failed nested admission must leave the enclosing LLM and
Home Assistant authorization contexts intact.

Guest policy is resolved before continuity and may tighten during a turn. A later
disable does not restore authority to a turn already restricted by Guest Mode.
The data scope and session keys are fixed for the claimed turn. Neither policy is
derived from another request's mutable entity fields.

Configuration has deliberately different boundaries:

- Request Rule overrides determine effective request options before dispatch.
- The existing `ProviderRequestSnapshot` assembles provider-controlled arguments
  and tools for the outgoing request. Its frozen descriptor contains existing
  container references; it is not a deep freeze of the entire configuration.
- The original configuration mapping remains an identity guard against a live
  edit followed by a revert. Runtime reconciliation and lazy Function Group
  validation retain their own narrow cache boundaries.
- Dispatch still checks authoritative current tool definitions, enabled state,
  groups, capabilities, exposure and authorization. A changed schema or executor
  cannot silently replace the tool advertised to the provider.
- Delayed work resolves current configuration and permissions when it becomes due.

Do not indiscriminately freeze these live checks or repeat validation on every
read. Preserve provider preparation caches and supported live updates.

## Execution and independent work

Prepared-call provenance and validation have different acceptance predicates.
Retained-origin lookup requires the exact input object. Reusing validation also
requires unchanged schema and arguments. A changed input must be revalidated
without losing its retained alias and replay protection. The existing two
validation snapshots are retained; the provenance consolidation adds no copies.

Effect receipts, recovery state, provider replay leases and child-operation paths
retain their own execution boundaries. Receipts are intentionally shared mutable
state for one admitted effect attempt; child paths and operation provenance are
task-local overrides. Their active leases prevent a surviving task from reviving
an expired owner. Caller cancellation never establishes that an effect did not
occur, and loss of a result never authorizes re-execution.

Administrator-authored local Rule work detaches from provider recovery accounting
while preserving its local authorization. A delayed job instead starts in a fresh
task context, restores its persisted Home Assistant user and reauthorizes live
execution. Clearing one request record would not replace that detachment boundary.

Maintenance leases, storage settlement, continuity ancestor activity, usage runs,
debug traces and completion queues retain their specialized ownership. In
particular, continuity ancestors intentionally share an active marker so a
descendant can observe its caller's completion. Converting that marker into a
copied immutable flag would change conversation-lock behavior.

## Regression obligations

The focused ownership suites cover nested and concurrent requests, cancellation
of either owner, repeated cancellation, setup failures and finalization failures:

- `tests/test_rule_action_ownership.py`
- `tests/test_request_prefetch_ownership.py`
- `tests/test_request_invocation_cleanup.py`
- `tests/test_turn_context_ownership.py`
- `tests/test_prepared_call_provenance.py`
- `tests/test_agent_registration_ownership.py`
- `tests/test_owned_agent_reload.py`
- `tests_real_ha/test_agent_registration_ownership.py`

Preserve their positive controls as well as rejection checks. Existing genuine
Home Assistant suites additionally establish native Script results, user and
Voice Identity precedence, Guest transitions, live configuration, receipts,
archives, delayed execution and reload behavior. Performance comparisons must
measure ordinary successful work as well as nested, failed and cancelled work;
isolated context-variable timing is insufficient to certify the whole pipeline.
