# PR4 Direct Rendering inventory

Baseline: live develop `a217de845ed480fa33038d6244c159b4a7d0c5e5`, with PR1/PR2/PR3 merged. Branch: `refactor/frontend-direct-rendering`.

## Inspection before editing

| Area | Current extra work | Classification / plan |
| --- | --- | --- |
| Configuration clarity | Scan every setting to relabel, re-label options, append search terms, remove/recreate effect badges; scan search results; observe owned settings host; queue another enhancement on every input | Move field/search presentation to markup; remove owned-DOM observer. Keep dirty-navigation coordination independent of field presentation. |
| Decision guidance | Scan defaults, rule cards and safe tester, create live tester and restore scope; run both synchronously and in a microtask | Render default badges, rule summaries/testers and restore scope in owners; keep explicit live-request binding and confirmation behavior. |
| Conversation default label | Render wrapper and microtask replace `Ha Default` in generated badges | Generate the final badge label directly. |
| Settings polish | Nested microtasks on render/input, prune badges just added by other layers, move model buttons into newly built cards | Generate final badges and model-data panel directly; retain unrelated Guide styling/layout helper. |
| Knowledge/Capabilities | Parse generated strings as templates to add badges/dialog controls or remove moved configuration fields | Direct markup candidates after settings hot path. |
| Copy polish | Shell-revision-only microtask scans labels, removes/moves nodes | Candidate; shell-only semantics differ from ordinary route renders, avoid incidental copy redesign. |
| Configuration guidance | Render/input enhancement adds dependency/model/provider notes; validates API choices asynchronously | Generic guidance retained per scope; backend validation is a real asynchronous dependency. Avoid broad removal. |
| Navigation/search and toolbar | Replace search/navigation and move controls after render | Broad helper redesign explicitly deferred to PR5; narrowly update result presentation where needed to remove clarity observer. |
| Overview health | Decorates health cards after rendering in a microtask | Independent candidate, not required for settings conversion. |
| History pagination | Post-render pager construction | Independent candidate; preserve lazy history and paging contracts. |
| Function repair | Lazy render wrapper binds repair actions | Binding/lazy behavior retained; installer consolidation belongs to PR5. |
| Debugging | Lazy embedded panel setup and debug-specific presentation | Separate component/lazy boundary, retained. |
| Provider credentials | Watches Home Assistant-owned config-flow dialog | External DOM observer retained. |
| Dirty state / lifecycle | Microtasks coordinate authoritative dirty tracking, editor deferred render, property replay, focus and selector events | Legitimate lifecycle/event ordering retained from PR2. |
| Search/debounce, model lookup, downloads, toasts | Timers/promises wait on user input, backend data, object-URL consumers, or notification lifetime | Genuine asynchronous behavior retained. |

No new render-wrapper architecture or framework will be introduced. Bootstrap and unrelated installers remain.
