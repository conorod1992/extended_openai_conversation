# Final setup and compatibility ownership audit

This cleanup preserves the post-D behavior without startup replacement of integration functions. Management and Debug directly register their versioned assets and websocket/panel steps, recording each successful step so a later failure can be retried. The shared asset owner still validates the checked-in manifest and enables caching.

Function Tool quarantine is imported explicitly by conversation and execution resolution; strict configuration validation remains strict. Request-local quarantine names and group filtering retain their existing ContextVar behavior. Management loading now only supplies catalogs, Overview projections, and configuration snapshots.

Startup no longer installs Management aliases, delayed-tool guards, request error boundaries, late Chat tool IDs, debug summary fields, or recursive Request Rule reference methods. Their existing owners now call the same safeguards. Native defaults are normalized at definition before config snapshots are created. Exposed-attribute defaults and validation belong to agent_config without captured originals or import-order repair. No storage version or API contract changes.

## Residual reassignment classification

The repository-wide Python AST/source sweep covered attribute assignments, setattr/delattr, captured originals, alias mutation, sys.modules and installer flags. No production `_INSTALLED` flag or package alias repair remains.

- **TemplateEnvironment.__init__**: retained in template.py. This implements the integration template global for newly created HA template environments. The manager tracks original/replacement identity, environment globals, subscribers and unload restoration. It is a lifecycle-managed feature; existing lifecycle, rollback and multi-entry tests remain.
- **OpenAI InputTokensDetails model field**: retained in openai_compat.py, gated to SDK 2.45.0 and the field being required. Compatible providers omit cache_write_tokens; the generated SDK model parses responses before integration call sites can adapt them. Its zero default and parent/child model rebuild preserve provider compatibility. This is the sole global compatibility schema mutation.
- **HA schema conversion**: compatible_to_openapi is integration-owned and called by HA tool schema conversion and structured output formatting. It preserves existing HA converter selection, optional Probatio support and translation of incompatible serializer UNSUPPORTED sentinels. It never assigns llm.to_openapi.
- **RestData._session**: retained as an instance-only bounded HTTP adapter in functions/web.py. HA RestData has no public session injection API; the proxy enforces response byte limits without changing HA's class or shared session.
- **Store._private/_atomic_writes**: retained as per-instance initialization before first I/O. Existing Store instances have no public setters. The helper also repairs existing file permissions; no Store class is modified.
- **agent_configuration setattr**: writes runtime lock/config/retry data to an agent instance using fixed internal attribute names. It does not replace methods or classes.
- **Other attribute writes**: normal owned data updates (usage counters, conversation identifiers, guest/voice context restoration, expiry timestamps, request budgets, config-entry runtime_data and the Skills singleton). These are state, not callable replacement. Debug/client/stream proxies are explicit object composition, not installed method wrappers.

## Dead-code verification and limits

Deleted lifecycle_optimizations.py after moving its only remaining ContextVar to conversation. Removed obsolete setup/install wrappers and tests that exercised their composition, retaining behavioral tests at the owners. `_overview()` and `_usageBar()` had definitions but no source callers: `_content()` selects the owned Overview renderer and `_usage()` selects the lazy Usage renderer. Deleted those methods and the now-unused tokenBreakdown import, and rebuilt the production bundle. A source/bundle regression test guards their removal.

Remaining small hardening modules contain called helpers, not empty installers. Dynamic frontend routes and HA callbacks were checked before removing code. The AST regression guards migrated callable/config names across production modules without banning ordinary instance state.

Excluded: provider-loop/tool-assembly redesign, frontend UX changes, storage migration, scheduler redesign, Continuity/Voice Identity changes and Request Rules feature work. The only adjacent runtime edits inline previously installed behavior.
