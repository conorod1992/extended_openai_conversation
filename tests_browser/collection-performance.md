# Persistent Functions and Request Rules collections

## Reproduce the structural benchmark

Install the repository's frontend and Playwright dependencies, install Playwright Chromium, and serve the desired checkout on `http://127.0.0.1:4173`. From the checkout containing the benchmark script, run:

```sh
node tests_browser/collection-benchmark.mjs /tmp/collections.json
```

The script loads the served checkout's browser fixture, seeds 40 Function Tools in four named groups (plus the ungrouped container), and independently seeds 40 Request Rules. It measures one confirmed toggle and one search input operation on each surface. To compare another implementation, serve that checkout but run the same benchmark script. The baseline for this PR is develop commit `35524d02ea390b8f4d3fff865d07226e64f69d34`.

`collection-performance.before.json` and `collection-performance.after.json` contain the actual Chromium/Linux measurements. They are structural counts, not elapsed-time or end-to-end latency claims. Both measurements use the same fixture and browser environment.

- `elementChanges` counts added and removed Element nodes, including their descendant elements. Attribute and text-only changes are not counted as element additions/removals.
- `records` includes all observed child, attribute and text mutations beneath main.
- `mainChildReplacements` counts direct child-list changes on main; zero proves the supported operation did not replace its route contents.
- `retainedCards` and `retainedGroups` count previously captured nodes still connected after completion.
- `renders` counts panel render calls, not list replacements. Supported mutations can call render while reconciling the collection without generating whole-route markup. Search uses no route render.

The corresponding browser tests also exercise tool edit/delete, current-name dispatch after indexes shift, group membership/create/rename/delete, HA availability refresh/add, rule edit/save/delete/create/move, current revisions, failed mutation rollback/retry, filtered counts and search focus, and single-fire actions after repeated updates. Existing delayed Function Tool editor loading/cancel/reopen coverage remains in the browser suite.

## Ownership and limits

Collection maps retain only DOM references and presentation signatures. Current panel draft/result and backend responses remain authoritative. Mutation handlers continue to use existing revision/serialization and confirmation paths. A changed tool or rule card may be replaced; unaffected cards stay connected. Group membership moves existing tool nodes. Group metadata updates its header and affected assignment choices without replacing tool cards. Request Rule order changes move existing cards and patch boundary buttons rather than invalidating card contents.

The small reconciliation hook is restricted to these two routes, a settled page, and closed editors. Dialogs retain their independent rendering lifecycle. Matching-settings/wording changes and non-isolatable repair/recovery continue to use the existing full-render fallback. Normal confirmed collection mutations and search preserve the collection host and search input.

Reconciliation still scans the current collection and compares presentation signatures; this is not an O(1) CPU implementation or virtualization. This PR removes disproportionate DOM reconstruction and repeated collection-event binding. Bootstrap/lazy ownership, global render invalidation, configuration-guidance hot paths, and Knowledge/Memory persistence are deliberately deferred. No framework, backend API redesign, or UX redesign is introduced.
