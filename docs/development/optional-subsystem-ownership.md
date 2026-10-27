# Optional subsystem ownership

Optional managers retain their own configuration, persistence formats and
initialization policies. Agent maintenance coordinates exclusive operations;
it does not manage subsystem availability or serialize their data.

## Live storage and permanent deletion

`RecoveryGuardedStore.bind_agent()` assigns both I/O fencing and live cleanup
ownership to the same `(provider entry, agent subentry)` maintenance gate.
The gate holds weak references to Store handles, so temporary Archive partition
handles and unloaded resources are not retained merely for bookkeeping. A
deletion snapshot holds its live handles strongly while cleanup awaits I/O.
Repeated binding deduplicates the handle; rebinding moves cleanup ownership
along with the existing I/O guard.

The regenerable embedding cache registers its plain HA Store separately. Its
best-effort persistence semantics are unchanged. Integration-global Stores,
including delayed-job queues and restore journals, are not adopted by an agent
merely because they use the same storage implementation.

Permanent deletion keeps the established order:

1. Retire request readers and acquire exclusive agent maintenance.
2. Remove that agent's delayed jobs and transfers, unload Rule conditions and
   drain manager-owned workers through their existing preparation APIs.
3. Snapshot live owned Stores, cancel their delayed/final-write callbacks and
   remove them through the storage compatibility boundary.
4. Scan the exact existing on-disk agent namespaces for remaining partitions,
   caches, unloaded data and restart orphans.
5. Evict manager caches and publish the deletion fence only after success.

Live handles are independent after worker preparation; their removal order is
not a cross-Store transaction. Failures retain the existing retry behavior.
The gate's resource snapshot requires no configuration or directory scan on a
conversation request. Cached gate lookup constructs no discarded replacement
owners.
Cold lookup retains the existing atomic publication through `setdefault`, so
concurrent construction cannot publish different maintenance owners for one key.

Adding an agent-bound guarded Store no longer requires teaching deletion its
manager's private attribute names. A new durable namespace still needs an
explicit orphan-discovery policy. Backup/restore schemas remain explicit and
separate: live handle discovery does not determine export contents or privacy.

Normal entity unload does not erase persisted data or stop a shared manager
needed by management, recovery or a replacement registration. Permanent deletion
is a distinct, exclusive operation; its Store removal API rejects callers that
do not own that maintenance boundary.

## Optional startup work

Memory, temporary memory, Knowledge and Archive keep their existing parallel
initialization and availability/error policies. The registration owns the four
initializer tasks until they settle. If one is cancelled or registration aborts,
it cancels unfinished siblings once and drains their cleanup before releasing
the maintenance lease, signalling readiness or admitting a replacement.
Repeated caller cancellation cannot interrupt that drain or replace its original
cancellation. Shared managers are not shut down by this task cleanup.

Disabled conversational capabilities do not imply their data is disposable.
Archive retention still prunes older data when collection is disabled; Knowledge
keeps its existing management/startup access. Live reconciliation retains its
configuration-identity fast path, immediate capability gating, manager reuse and
retry semantics. This boundary adds no work to ordinary conversation requests.
