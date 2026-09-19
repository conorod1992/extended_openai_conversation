# Conversation request-entry ownership

`ExtendedOpenAIAgentEntity._async_process` is the stable request entry owner.
Both Assist (`async_process`) and the direct process action
(`async_process_direct`) call it. Setup and feature modules must not replace it,
its entry stages, or its scope helpers through assignment, `setattr`, deletion,
or wrapper installation.

## Deliberate order

The previous effective nesting is retained, outside to inside:

1. Bind the originating Home Assistant caller context.
2. Acquire the selected agent's shared maintenance lease.
3. Begin opt-in request debug capture.
4. Establish a fresh request-local formatted-tool cache.
5. Prefer the device-registry source, validate configured Voice Identity users,
   and bind their eligible ownership IDs.
6. Reconcile current runtime configuration.
7. Resolve guest policy, data scope and continuity, then process the claimed turn.

Scope lifetimes are visible as ordinary context-manager calls in the owner.
They are not a configurable hook list, plugin registry, callback dispatcher, or
replacement pipeline. Reusable managers remain in their existing modules.

HA authorization remains distinct from Voice Identity: selecting a personal data
owner never changes the authenticated caller context used by tool permissions.
The satellite source is temporarily hidden only when a device-registry ID is
also available, and is restored on success, failure and cancellation.

Maintenance admission occurs before live configuration reconciliation and Voice
Identity lookups. Debug begins after maintenance admission but before input
normalization, so its input capture retains the original satellite metadata.
Debug finalization occurs after the voice/cache scopes restore their callers,
but before the maintenance lease and authenticated caller context are released.

`_async_process_with_continuity` owns guest-policy and prompt-cache tokens and
releases a claimed continuity turn in `finally`. The guest token now also resets
when scope resolution or timeout conversion fails before a claim is attempted.
The existing shielded continuity release is preserved.

## What remains outside this change

This change removes only the request-entry portions of configuration lifecycle,
HA permissions, maintenance, debug, request-static caching and Voice Identity
installation. The other runtime installers still own their existing inner seams:
tool execution, memory retrieval and Temporary Memory, provider loops,
summarization, prompt caching/formatting, result compaction and inner phase
instrumentation. Those are the separate PR6 work, not a new generic framework.

Request Rule matching, local-intent precedence, stored data, provider payloads,
Management dispatch and frontend rendering are unchanged.

## Regression boundaries

The unit suite checks source-level replacement forms, the explicit order shared
by Assist/direct processing, cleanup, request isolation and continuity release.
The genuine-HA suite remains the final validation for actual setup/reload and
both public processing paths.
