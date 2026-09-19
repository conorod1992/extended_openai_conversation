# Frontend renderer composition

PR7 starts at `develop` `a864e9a3` (including #579–#581). The preservation
baseline is rendered browser output, including the former wrapper layers.

| Surface | Content / presentation owner | Behavior owner |
| --- | --- | --- |
| Guide | `guide-content.js`, ordered groups in `guide-page-impl.js` | `bindGuide`; lazy `guide-page.js` facade |
| Request Rules | `request-rules-ui-impl.js`; explicit query and in-place search option | Existing rule, routing and tester binders |
| Configuration | `agent-config-editor-base.js`; pure model decisions in `agent-config-model-presentation.js` | Model lookup, configuration and exposed-attribute binders |
| Function Groups | Base group/tool cards, including assignment controls; HA cards accept their assignment markup | Existing group save and native-module assignment binders |
| Export / Restore | Pure templates in `backup-transfer-ui.js` | Existing transfer, inspection, scope and confirmation workflow |
| Voice | Lazy `renderVoiceIdentity` supplies its section body directly | Existing native HA picker and registry bindings |

Route selection happens before rendering: Conversation excludes local handling,
Web & Skills selects its own fields, Model excludes moved Memory controls, and
Voice composes its existing renderer. The panel only wires the lazy Voice body;
it does not parse or rearrange configuration markup.

Guide search aliases retain terms from the previous definitions, including
material removed from the visible Usage topic. Search-only destination groups
retain their previous ordering and descriptions. The backup/debugging topics
retain their original search-term matching behavior.

Configuration caches remain bounded and restricted to clean stable sections.
Their dependencies include route, result, draft, assistant, model data,
capabilities and Voice scopes/renderer. Dirty drafts and model sections are not
cached. Actual YAML parsing, native editor initialization, stale-response guards,
focus restoration and async capability lookup remain behavior responsibilities.

## Existing copy discrepancies

- Guide says deterministic rules are evaluated in displayed order; Request Rules
  says order is the final tie-breaker after match type and specificity.
- Guide describes a per-request tool budget; the configuration label is
  “Tool-call limit per conversation”. Backend keys and semantics are unchanged.
- Several former Guide replacement strings contained raw apostrophes and never
  matched escaped browser HTML. In particular, the visible Guide still described
  Hassil while the rule dialog described ExtendedOpenAI syntax. This cleanup
  preserves the shipped text; a separate documentation/semantics decision is needed.

The local-handling source now consistently owns the requested exception-list
presentation. Previously the Home Assistant route called the implementation
directly, bypassing the facade's newer explanation and delayed-command layout.
The separate superseded toggle is no longer generated.

Unrelated post-insertion copy polish for Overview, history and usage remains in
the loader; it is not part of configuration render composition. Function repair
retains its separate degraded-state UI ownership and supplies cards directly to
the tools renderer. Backup transport utilities and
legacy import bindings still have consumers and are not removed with decorators.

Regression coverage inspects immediate output, prohibits template creation during
production rendering, and exercises real browser interactions. Editor loading,
native YAML, unsaved-state, lazy ownership and timing-isolation suites remain enabled.
