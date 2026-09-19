# Runtime ownership audit

PR6 starts from PR5's merged develop commit `75d5aeafba184dcca5e7bd1019fd275e99224e26`.
The request-entry owner established by PR5 remains `ExtendedOpenAIAgentEntity._async_process`;
its source and request/context ordering are unchanged.

## Tool and memory owners

| Owner | Explicit responsibilities |
| --- | --- |
| `ExtendedOpenAIBaseLLMEntity._execute_function_tool` | Configured argument validation, HA LLM dispatch, durable delay scheduling/replay, execution errors, and compatible result construction. |
| `tool_exchange` validation and execution boundaries | Recoverable validation before dispatch, exact prepared-call binding, serial/parallel order, budgets, and completion of retained calls after failure or cancellation. |
| `ExtendedOpenAIAgentEntity._execute_function_tool` | Dispatch to configured/integration tools, bounded and compact model results through the existing HA semantic compatibility helpers, and attempt diagnostics in `finally`. |
| `_async_dispatch_function_tool` | Live configured-tool resolution, existing Guest authorization, integration dispatch, and subsystem-specific error labels. |
| `_async_retrieve_memories` / `_async_select_memories` | Live memory gate, embedding-provider synchronization, Temporary Memory prefetch and cancellation cleanup, retrieval diagnostics, and existing scoped/pinned memory selection. |
| `_async_retrieve_temporary_memories` / `_async_load_temporary_memories` | Live capability and retained-owner checks, one-time prefetch consumption, disabled-prefetch cancellation/draining, and owner binding/reset around manager access. |
| `_async_execute_memory_tool` | Live enablement, search validation, scoped operations, and model-only sparse record projection. |
| `_async_execute_temporary_memory_tool` | Retained-owner and live enablement gates, existing Guest policy, scoped operations, and unconditional owner-token cleanup. |
| `_async_execute_archive_tool` | Live enablement, search validation, scoped operations, and Archive-specific failure containment. |
| `_async_execute_knowledge_tool` / `_async_rank_memories` | Existing blank-search guards and existing operation-specific model projections at source. |

Recovery-validated calls carry the exact input identity plus copies of the validated schema and
arguments across dispatch. Direct calls, different inputs, or changed definitions/arguments
validate locally. This is a narrow validation contract; it registers no callbacks or hooks.

## Removed installation machinery

- Entire `configuration_lifecycle_hardening` module, installer, and installation flag.
- Delayed tools' `_install_execution_hook` and its marker; the old unreachable composite-delay builder.
- Runtime result hardening's installer-of-an-installer and outer conversation result wrapper.
- Archive failure-label installation around Function Tool execution.
- Memory prefetch installation around the two retrieval methods.
- Temporary Memory's conversation-method ownership installer.
- Model-search agent installer, per-method markers, and installation flag.
- Model-result compaction installer and installation flag.
- Memory retrieval tracing wrappers and Function Tool timing wrapper.
- Bootstrap imports/calls for the removed installers.

Structural tests forbid reassignment/deletion of the owned runtime methods, including obvious
class/name/setter aliases. Repeated setup tests and genuine-HA setup/reload acceptance protect
method identity. Behavioral tests call owners and mock I/O rather than rebuilding removed wrappers.

## Deliberately retained boundaries

This completes the fresh-sweep ownership series for Management API (PR4), conversation entry
(PR5), and the tool/memory execution seams (PR6). It does not claim every integration installer
has been removed. The following remain substantive, separate work:

- Durable manager transaction shielding, rollback, initialization, private Store handling,
  Temporary Memory storage-owner migration/snapshot contracts, and deferred expiry writes.
- Provider request/stream loops and stream ID repair, request preparation failure handling,
  prompt rendering/caching, context summaries, usage accounting, and diagnostic presentation.
- Guest/skill manager lifecycle, native function resource/authorization guards, delayed scheduler
  lifecycle and stored-call recovery, maintenance and backup/restore architecture.
- Management API, frontend, configuration UI, Conversation Continuity, Voice Identity policy,
  and Request Rules.
- Version-gated OpenAI compatibility and HA schema/tool-result/service compatibility.

Stored data formats and service/API contracts are unchanged. There is no generic hook registry,
middleware stack, or dynamic replacement pipeline.
