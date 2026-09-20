# Feature-local actions

Base: `2dcf746b757b6aa4030a3e85e0236700b9262ded` (merged lazy interfaces).

The Temporary Memory feature calls its own open/close/save/delete functions.
Those functions are now private; their only production callers are local event
handlers and operations. The shared dialog coordinator still calls the feature's
dirty predicate through the existing lazy registry, so no Memory implementation
is imported eagerly into shared safety code. Five panel forwarding methods are
deleted. Confirmation, pending-save guards, draft reset, error handling, mutation
refresh, notifications and collection ownership retain their existing logic.

History owns its session/search event binding and calls its own session operation
from turn pagers. The two remaining panel forwarding methods are deleted, and the
session/search operations no longer need exports. Binding is marked on the owned
archive card so repeated binding does not duplicate handlers. Keyboard activation
continues to use the shared panel helper. Session load tokens, dialog checks,
pagination limits, search state and errors are unchanged.

Retained: `management-memory-feature.js` reconciliation/metadata ownership,
shared dialog/confirmation and mutation services, and shell-owned actions that
do not create a feature-to-panel-to-feature round trip. No broader panel
reorganization or renderer consolidation is included.

Browser coverage runs source and built Temporary Memory actions, including dirty
Escape/close cancellation, discard, failed-save recovery, forced duplicate submit,
successful cleanup and dialog deletion. Existing collection, scope/agent change,
refresh and lazy-loading suites remain. History checks exercise repeated binding,
keyboard activation, search paging and turn paging with exact request counts.
Measurements use the Git-blob method in `frontend-cleanup-copy.md`.
