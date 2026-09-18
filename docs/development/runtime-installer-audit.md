# Runtime installer ownership audit

Audited after performance ownership PR #572, against develop commit `05ce902951ab042ad639b1f422ebf24000735f58`.
Scope: backend `install_*` functions, installation flags, saved original callables, marker attributes, runtime assignments, import-time registration, and startup ordering.

## Removed machinery

- `durable_state_hardening.py`: its only installer toggled `_INSTALLED`; archive transactions and retention already belong to their owners. Removed the module and its persistence bootstrap import/call.
- `lifecycle_optimizations._install_archive_fast_path`: duplicated the early `archive_enabled` return in `ConversationArchive.async_begin_session`. Removed its saved original, method reassignment, and installer call. The disabled-archive test now exercises the owner without installing anything.
- Duplicate `__init__.async_setup` calls/imports for safety hardening and management optimization. Their first activation remains in the persistence and Guest chains respectively, preserving effective installation order.
- Comments implying the deleted performance installer must run before strict cached configuration validation. The validators are ordinary `agent_config` implementations now.

Usage getter initialization, persistence, pruning, and archive journal ownership have no obsolete installer left to remove. `agent_configuration.py` and `conversation_lifecycle.py` own real configuration/lifecycle work; they are not compatibility proxies. Phase 1 already deleted `performance.py`, including its installer and flag.

## Remaining runtime work for later ownership tasks

These modules still install substantive behaviour. Their flags, markers, and captured callables prevent duplicate wrapping or delegate to a live implementation; removing them alone would change behaviour.

| Modules | Behaviour retained |
| --- | --- |
| `configuration_lifecycle_hardening` | Request-boundary configuration reconciliation, embedding-provider synchronization, and live memory/archive enablement gates. |
| `persistence_hardening` | Transaction rollback, cancellation shielding, retryable initialization, private Store preparation, and delayed-tool retry budgets. Memory's own rollback does not replace the outer cancellation shield. |
| `runtime_hardening`, `runtime_failure_hardening`, `safety_hardening` | Skill/Guest initialization, bounded tool results, preparation errors, archive failure labels, streamed tool-call repair, delayed caller identity, native authorization/resource bounds, and broadcast transactions. |
| `lifecycle_optimizations`, `hot_path_cleanup` | Temporary-memory prefetch/cancellation and debug summary fields; single debug-event conversion and broadcast cold-path checks. |
| `temporary_memory_performance`, `temporary_memory_ownership` | Deferred expiry writes and retained-owner isolation across manager, snapshot, conversation, and management paths. |
| `guest_performance`, `request_static_cache`, `skill_runtime_availability`, `voice_identity_runtime` | Guest policy reuse, request-local formatted tools, skill availability, and voice scope binding. |
| `context_summary_performance`, `context_usage_hardening`, `input_footprint` | Deferred summaries, provider/local token accounting separation, and request sizing. The Usage accounting wrapper is distinct from already-owned Usage persistence. |
| `debug`, `request_diagnostics`, `debug_ui` | Turn/provider tracing, payload/latency metrics, and diagnostic activation. |
| `exposed_attributes` | Configuration-field registration, selected attribute rendering, and management decoration. Import ordering remains necessary for consumers of the configuration snapshot. |
| `management_loading_performance`, `management_function_quarantine` | Cached management projections, static routes, persisted-tool quarantine, and provider fallback. |
| `feature_status`, `management_setup_health`, `management_configuration_guidance`, `management_history_runtime`, `management_browser` | Effective subsystem status, health reporting, configuration guidance, bounded history, and browser routes. |
| `management_permissions`, `function_dependency_integrity`, `request_rule_match_preview` | Management authorization, dependency-safe mutations, and rule previews. |
| `agent_maintenance`, `restore_recovery`, `delayed_tools`, `ha_permissions` | Maintenance exclusion, recoverable restore, delayed execution, and HA caller-context propagation. |
| `regex_execution`, `model_search_hardening`, `model_tool_results`, `functions/web` | Isolated configurable regex execution, bounded search/results, and bounded HTTP session adapters. |

The remaining `_async_process` layers are deliberately not consolidated. A later ownership task should preserve their context nesting, exception/cancellation handling, and authorization order.

## Compatibility and bootstrap retained

- `openai_compat`: version-gated OpenAI 2.45 Responses usage field compatibility.
- `ha_llm_tools`, `ha_tool_result_compat`, `ha_actions`: HA schema serializer, tool-result, and service helper compatibility across supported versions.
- `native_function_schema_migration`: historical persisted tool schema migration and current default normalization; this still changes data and is not a no-op installer.
- Usage legacy storage mirrors, archive recovery, and temporary-memory legacy owner migration remain needed for persisted data.
- Frontend module registration in `__init__` still supplies actual static routes. Debug assets must precede panel setup; management authorization must wrap optimized dispatch; maintenance must wrap the final request entry point; delayed-tool result/identity guards must retain their existing order.
- Guest startup remains the single management optimizer activation path. Persistence startup remains the single safety-hardening activation path. Tests cover these paths without relying on the removed duplicate calls.
