# Frontend asset registration ownership

The complete Management asset registry is
`management_ui.MANAGEMENT_FRONTEND_MODULES`, an immutable, `Final` tuple. It
includes direct imports and lazy route/implementation dependencies. Adding a
module here makes an HTTP route available; it does **not** eagerly execute the
module in the browser.

## Scope of this refactor

Baseline: `develop` at `5fc9815`, after the Knowledge-panel retirement (#579)
and lazy-feature ownership (#580).

The effective Management registry contained 57 unique modules. Its declaration
listed only 45; 12 more depended on imports or feature installers. The same
57-module set is now declared in one place. Nine mutation sites and five
registration-only helpers are removed from integration startup, feature status,
Temporary Memory, configuration guidance, Function Tool quarantine, history,
loading optimizations, permission setup and setup health.

No backend dispatcher/runtime wrappers, authorization checks, retry markers,
cache behavior or browser rendering are changed. The standalone Memory files have since been removed after unified parity, as
documented in the standalone-panel retirement audit.
The Knowledge panel is not reintroduced.

## Registration boundaries

- Management setup consumes the complete tuple directly, with or without the
  optional cached-asset setup replacement.
- Cached setup still exposes each module through its unversioned no-cache alias
  and its versioned cacheable URL.
- Debug keeps its existing separately owned routes (`debug-panel.js` and
  `debug-management.js`) and setup-before-Management ordering. This PR does not
  move its endpoint ownership or broaden access.
- Feature installers must not append to, replace or otherwise assemble the
  Management registry. Backend installation order must not determine asset
  availability.

## Regression coverage

- Compare the literal declaration with the imported runtime tuple and the
  shipped module inventory; reject duplicates and missing files.
- Reject assignments and `setattr`/`delattr` writes to the registry outside its
  owner.
- Check static **and dynamic** ES-module imports against the actual declaration,
  rather than collecting arbitrary JavaScript filenames from Python sources.
  Negative cases prove missing direct/lazy dependencies fail this check.
- Preserve asset tuple identity across repeated history/loading installation.
- Verify every registered module retains both URL/cache-policy contracts.
- Load the real integration in Home Assistant, repeat UI setup, fetch every
  declared asset and the Debug entry point through both URL forms, compare
  response bytes, and ensure legacy panel assets remain unavailable.
