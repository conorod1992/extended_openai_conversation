# Lazy frontend feature ownership

Baseline: `develop` at `7232aee041ebda074809c39ebb47132e794c4404` (after #578).

## Ownership

Route imports publish feature modules; they no longer receive a panel constructor or
replace class methods. Existing import-promise deduplication, load tokens and
parallel backend/asset loading are retained.

| Feature | Explicit owner |
| --- | --- |
| Quiet Hours | Panel content and action binding call `renderQuietHours` / `bindQuietHours` |
| Function repair | Panel chooses fallback versus ordinary content before rendering; repair binding runs on each completed/preserved render |
| Usage | Panel owns daily-page dispatch, Usage rendering, details dialogs and bindings |
| Input footprint | Usage composes its markup and retry binding; asynchronous footprint loading stays route-owned |
| Provider credentials | Panel owns observer teardown before render, on disconnect, and restart on reconnect |
| Request Debug | Panel renders/binds the embedded element; the Debug class owns its embedded presentation, assistant selection and bounded paging |

The Usage paging helper pins the selected assistant for every page. The embedded
Debug class selects the Management assistant before loading runs rather than
loading another stored selection and then correcting it. Standalone Debug retains
its visible picker and unpaged request/copy behavior.

## Preserved boundaries

This is not the legacy-panel retirement or asset-registry consolidation. No Python,
backend WebSocket, stored-data, legacy Memory/Knowledge or registry code changes.
The existing provider diagnostic-result observer remains active and has explicit
lifecycle ownership; genuine async requests, focus and draft scheduling remain.

The debug adapter has no custom-element subscription or installation side effects.
It retains pure paging-label helpers and a dynamic import of the Debug element.
The Debug element can import those helpers without eagerly loading it elsewhere.

## Regression coverage

- Load every lazy feature with a frozen Management prototype and a constructor
  getter that throws if a loader accesses it.
- Verify both Management and Debug method identities remain unchanged.
- Assert daily pages stay pinned when the selected assistant changes mid-request.
- Exercise native embedded/standalone Debug markup and request contracts.
- Browser journeys cover repeated route visits, bindings, bounded Debug paging/copy,
  Usage retry/details and credential-observer disconnect/reconnect/navigation.
- Retarget old source-location assertions to the actual owning renderers without
  dropping their behavioral requirements.
