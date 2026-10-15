# Subsystem lifecycle APIs

Deletion and restore use different operations. These APIs encapsulate existing
behavior; they do not define a general shutdown or resource ownership framework.

| Operation | Owner | Contract |
| --- | --- | --- |
| `async_prepare_for_deletion()` | Usage | Cancel and await pending pruning, then mark stopping and release retention and HA stop callbacks. Do not flush totals, days, or details. |
| `async_prepare_for_deletion()` | Temporary Memory | Cancel and await pending prune persistence. Do not flush records. |
| `reset_all()` | Continuity | Clear sessions, memory bundles, pending ends, and ignored conversation IDs. Retain counters and HA-default lock/claim bookkeeping. |
| `reset_all()` | Function Groups and Request Rules | Use the existing generation/token invalidation APIs introduced with shared conversation request bookkeeping. |

The caller must hold the agent's exclusive maintenance lease after draining
request readers. Preparation does not erase stores or detach registry entries.
The deletion coordinator still removes delayed tools and transfers first, prepares
managers in its existing order, cancels native Store delayed/final writers, removes
owned handles and discovered files, and finally detaches registries and marks the
maintenance gate deleted. Failed erasure remains retryable; the deleted gate stays
as a fence after success. Preparation is safe to repeat. Prune-task exceptions
during cancellation remain settled with `gather(return_exceptions=True)`.

Usage's ordinary `async_shutdown()` remains a separate operation that drains
active runs and flushes statistics. Using it for permanent deletion could write
data that the user requested to erase. Temporary Memory's ordinary retention and
unload behavior are unchanged.

Restore still resets both distinct registry and live-agent instances, then makes
the live instance authoritative. Manager acquisition order, restored durable
state, Usage fallback replacement, sensor rebinding, quarantine, and recovery
journals remain the coordinator's responsibility.

Static durable namespaces and store-handle discovery remain unchanged, including
cleanup of never-loaded managers. A future ownership-inventory change should
replace those inventories rather than add a parallel source of truth.

Focused tests independently seed retained and discarded state, exercise pending,
finished and missing prune tasks, and distinguish deletion from normal shutdown.
Genuine HA tests load the integration, use native Store files, check sibling
isolation and erasure retries, and restore a loaded agent. Existing deletion tests
also cover unloaded and disabled providers and orphan rediscovery.
