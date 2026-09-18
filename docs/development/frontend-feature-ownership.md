# Frontend PR3: Feature ownership

Based on live `develop` at `1fa45292` (PR #574). This migration preserves the existing markup transformations and backend contracts.

## Effective order before migration

Bootstrap installed copy polish, Guest browsing, state safety, feature status, temporary memory, memory settings, Capabilities IA, then Voice Identity, followed by visual decorators. History pagination installed lazily on first conversation entry.

| Feature module | Removed runtime assignments | Explicit owner |
| --- | --- | --- |
| guest-mode-ui | `_formatDate`, `_loadSection`, `_memories`, `_conversations`, `_guestPolicyView`, `_updateVisibleList`, `_searchArchive`, `_openSession`, `_bindActions` | Panel calls memory browser preparation/completion, rendering, filtering and binding helpers. Superseded archive/session implementations removed. |
| management-temporary-memory | `_loadSection`, `_scopePicker`, `_memories`, `_dialogs`, `_openTemporaryMemory`, `_temporaryMemoryDirty`, `_closeTemporaryMemory`, `_saveTemporaryMemory`, `_deleteTemporaryMemory`, `_bindActions` | Panel selects the temporary branch, delegates editor actions and uses the existing dirty/close contract unchanged. |
| management-feature-status | `_memories`, `_knowledge`, `_diagnostics`, `_testAgent`, `_styles` | Panel composes status markup/styles and delegates diagnostic actions. |
| management-memory-settings | `_isDraftView`, `_canAccessView`, `_content`, `_bindActions` | Panel owns route classification/access; helper retains model-reset binding and existing markup transform. |
| management-capabilities-ia | `_isDraftView`, `_configSectionsForView`, `_content`, `_knowledge`, `_dialogs`, `_knowledgeValues`, `_setKnowledgeEditorDisabled`, `_openKnowledge`, `_bindActions` | Panel owns page composition and editor availability; module exports configuration/source transforms and feature binding. |
| management-voice-identity | `_content`, `_bindActions` | Panel calls the existing lazy `voice-identity-ui` implementation directly; empty installer module and asset registrations removed. |
| management-history-pagination | `_searchArchive`, `_openSession`, `_render` | Existing lazy module exports search/session operations and pager decoration; panel invokes them directly. |

Seven installers and 42 runtime method assignments are removed. No replacement installer or runtime method-copy mechanism is introduced.

## Composition invariants

- Temporary memory bypasses persistent memory markup/status and limits scope to Personal/Shared. Persistent memory retains indexed filtering, debounced backend search and load-more behavior.
- Conversation search/session paging uses the lazily loaded history implementation (list limit 50, search/turn limits 20). Existing load-more presentation and DOM pager decoration remain; they are not redesigned here.
- Knowledge status precedes library markup, source badges decorate that result, and the page availability setting precedes the guide/library. Knowledge dialogs combine the existing core/temporary markup before availability decoration.
- Memory controls are removed from Model/Voice before Voice Identity transforms the Voice page. Home Assistant and Web search & Skills retain their explicit configuration sections.
- Feature bindings run once when the existing renderer replaces main content. Normal equal-markup rerenders retain existing nodes/listeners.
- Voice, memory settings and history assets remain lazy. Existing rendering decorators still run behind `_renderContent()`; history's post-render pager is invoked after that boundary returns.

## Deliberately retained

`management-state-safety.js` is unchanged. Navigation confirmation, beforeunload, dirty-state coordination, teardown and focus/reload behavior remain owned by that implementation. Temporary-memory dirty/close functions only change from installed methods to directly delegated helpers; their logic and calls to `_confirmEditorClose` are unchanged. This is the main editor seam to reconcile with the concurrent unsaved-state PR.

Copy polish, navigation search, toolbar/layout, configuration clarity/guidance, decision guidance, conversation labels, settings polish and Overview decoration remain. Lazy Function repair, Quiet Hours, Usage/input footprint, debugging and provider credentials remain outside this focused migration. Configuration stripping, source badge insertion, dialog availability insertion and history pager DOM decoration are intentionally retained for later Direct Rendering work. Bootstrap remains.

Regression coverage combines the existing shipped browser journeys (including permissions, lazy assets, Voice, Guest Mode, agent switching and editor guards) with feature-specific tests for memory kinds/scopes/load-more, Knowledge availability payloads and history paging. The new journeys rerender before actions and assert single requests/pagers.
