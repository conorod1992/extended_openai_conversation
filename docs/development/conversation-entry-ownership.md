# Conversation request-entry ownership

`ExtendedOpenAIAgentEntity._async_process` owns the complete request boundary. Both
Home Assistant Assist (`async_process`) and the direct process action
(`async_process_direct`) use it. Setup and reload must not replace these methods.
There is no dispatcher registry, callback list or generic hook framework.

## Effective order

The owner preserves the former effective nesting, from outermost to innermost:

1. Bind the actual Home Assistant caller context.
2. Acquire the selected agent's shared maintenance lease.
3. Start opt-in debug capture using the original incoming request metadata.
4. Establish a fresh formatted-tool cache for this request task.
5. Prefer the device-registry source, validate configured Voice Identity users,
   and bind the eligible data owners.
6. Reconcile live runtime configuration after maintenance admission.
7. Resolve the guest policy and data scope, claim continuity, process the turn,
   and release the continuity claim.

The body is readable ordinary control flow. Each named context manager owns one
specific resource or context lifetime; none accepts a next-handler callback.

Voice Identity selects data ownership, not permissions. A mapped/default user
must never replace the actual HA caller context used for entity and tool checks.
Registry-device preference still restores the original satellite metadata on
success, failure and cancellation, including failure during user validation.

Debug finalization occurs after the formatted-tool and Voice Identity contexts
are restored but before the maintenance lease and caller binding are released.
A request waiting for maintenance does not begin capture, perform identity checks
or reconcile configuration until admitted.

The continuity owner restores guest-policy and prompt contexts even when scope
resolution or timeout parsing fails before a claim is obtained. This closes an
existing early-failure cleanup gap. Claimed turns retain the existing shielded,
exactly-once release path; this change does not redesign continuity or shutdown.

## Remaining runtime ownership

PR5 removes only request-entry replacement from the permission, maintenance,
debug, static-cache, Voice Identity and configuration-lifecycle modules. Their
remaining tool/retrieval/inner-phase behavior is not moved into this PR.

Function Tool execution, memory retrieval, Temporary Memory runtime, inner
conversation lifecycle, context summarization, model-result compaction and prompt
cache implementations remain PR6 work. Standalone Memory, Management ownership,
frontend rendering and stored-data formats are unchanged.

## Regression contract

Structural tests reject assignment, deletion, `setattr`/`delattr`, and explicit
attribute-map replacement of the entry methods or their named lifetime owners.
Repeated setup and genuine HA reload preserve entry identity. Both public APIs
are exercised, including local completion without provider I/O.

Behavior tests check stage order, maintenance waits/cancellation, original caller
identity, mapped-owner isolation, concurrent request caches/capture, direct-action
metadata, failures during identity/reconciliation/processing, early continuity
failures and claim release. Migrated feature tests call the actual owner or its
ordinary resource helpers rather than reconstructing the removed wrapper stack.
