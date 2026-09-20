# Complete lazy feature interfaces

Base: `e27ef2b78ef861b737d9b274b716da592687a8f2` (merged copy cleanup).

`management-route.js` is the sole loader for Agent Config and Request Rules.
Both now use its existing feature module/promise maps, including single-flight
imports, cached publication, rejected-promise cleanup, route readiness and
parallel data/asset loading. The existing route token protects stale completion.
The shell still owns unloaded placeholders, section errors, empty unavailable
dialogs and false reconciliation results. Import failures retain the existing
fresh-page recovery behavior; deleting redundant facade queues does not remove
the route's error path.

`agent-config-editor.js` is a small complete feature entry: it retains markup
caching, all original invalidation identities and bounded cache size, optional
Voice presentation, backup/exposed-attribute bindings, and backup credential
warnings. It explicitly exports the render/dialog/collection interface and native
tool binding. Pure-helper tests import the base owner instead of maintaining
production forwarding functions. Model-aware binding and native YAML remain
separate behavior owners, without wildcard re-export chains. YAML fallback,
cleanup, generation checks and HA editor discovery are unchanged.

`request-rules-ui.js` composes rule binding, decision guidance, model/routing
controls and the match tester. The renderer defaults now capture the existing
query/in-place-search inputs directly. Collection reconciliation, model lookup
revision checks and routing behavior are unchanged. Pure-helper tests import the
implementation owner. Neither heavy implementation enters the shared bootstrap.

Deleted: `agent-config-loader.js`, `request-rules-loader.js`, repeated editor
loader state, forwarding functions and Node-only eager-load accommodations.
Retained: the two-line configuration-feature barrel (already a concise lazy
composition boundary), the native adapter and model-aware bindings (real behavior).

Coverage includes the complete route-published interfaces and repeat visits,
cache invalidation, cold source/bundle isolation, parallel requests during delayed
imports, navigation away, failed feature imports and fresh-page recovery, backup,
exposed attributes, routing/model controls, match/live testers, search/reconciliation,
and native YAML/fallback cleanup. The production bundle is rebuilt and audited.
Measurements use the Git-blob method documented in `frontend-cleanup-copy.md`.
