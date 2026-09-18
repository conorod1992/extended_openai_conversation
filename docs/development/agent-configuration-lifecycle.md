# Agent configuration lifecycle

Baseline inspected: `develop` at `e31db84b`.

## Effective behavior before ownership refactor

`async_setup_entry` installs persistence transactions first. That installer installs
configuration lifecycle handling, runtime failure handling, runtime hardening,
safety hardening, lifecycle optimizations, and finally durable-state handling.
Performance, debug instrumentation, and the maintenance barrier follow in entry
setup. The latter wrap request processing and retrieval, not entity startup.

Startup executes the original entity method, then synchronizes the shared memory
embedding provider, then registers daily archive retention with entity removal.
The original method registers the agent, loads skills, Usage and guest state,
prunes Usage, resets function-group runtime, and loads request rules. It then
initializes temporary memory, archive, Knowledge and persistent memory in that
order. Knowledge loads even when disabled; archive storage is skipped only when
both retention and model search are disabled. Optional manager failures publish
subsystem status and continue; core setup failures propagate. Cancellation is not
caught by the optional `Exception` handlers, and prevents subsequent startup work
and retention registration. The retention callback catches ordinary pruning
failures, reads current configuration each time, and propagates cancellation.

The provider wrapper avoids repeating an unchanged provider/model assignment
(including diagnostic status updates), clears pending maintenance on provider
removal, and replaces bound providers from old entities after reload. Startup
synchronization clears stale Hybrid providers when Lexical is selected. Retrieval
synchronizes again for live model/mode changes, inside the disabled-memory gate.

Configuration reconciliation runs inside the request preparation failure boundary
and outside the original request body. It uses data identity and a retry flag as
its fast path, serializes initialization with a per-entity lock, gates disabled
managers before awaits, updates streaming/Usage values, retries missing optional
managers, synchronizes the provider, and publishes its snapshot only on completion.
Cancellation leaves that snapshot unpublished and releases the lock. Manager getters
own shared-instance initialization; successful managers are reused on retry.
Streaming capability is also derived from live configuration before HA starts a turn.

The timeout wrapper accepts integers from 1 to 1440, coerces legacy integer strings
and integral floats, and retains five friendly UI presets. Booleans remain invalid.

## Refactor boundaries

Promote startup provider synchronization, archive initialization gating, retention
scheduling, streaming capability, timeout normalization, and provider idempotency
to their owners. Keep runtime reconciliation as an explicit configuration helper.
Preserve the request-boundary wrapper's position: moving it into the core process
method could change failure handling and instrumentation. Memory/tool enablement
gates, prefetch, maintenance, debug, and other request wrappers remain separate.

Behavioral tests cover normalization, provider transitions and scheduling,
reconciliation retries and identity, startup failures, cancellation and reload.
Installer-only timeout and retention tests should instead exercise their owners.
