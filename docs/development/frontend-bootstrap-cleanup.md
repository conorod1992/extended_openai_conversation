# PR5 frontend bootstrap and installer cleanup

Baseline: `develop` at `0b203d472954ab2e3d9d7f9928871cbc3235873c`, after frontend ownership PRs 1–3 and Direct Rendering PR4.

## What changed

The final management bootstrap no longer patches `ExtendedOpenAIManagementPanel.prototype` at runtime. The panel now composes the surviving behavior explicitly after its owned render/binding work.

Removed runtime installers:

- copy polish
- navigation/search
- toolbar layout
- configuration clarity
- configuration guidance
- decision guidance
- settings polish
- overview health/model-data clarity

`management-bootstrap.js` and its served-asset registration are deleted.

The former bootstrap order is preserved explicitly in `management-panel.js`:

1. shell-only copy polish scheduling
2. navigation/search projection
3. toolbar layout
4. configuration clarity binding/projection
5. configuration guidance binding/projection
6. settings/Guide layout polish
7. overview health/model-data follow-up

Decision confirmation scope is owned directly by `_confirm()`. Search-configuration reset and configuration-destination projection are owned directly by the panel rather than injected by installers.

## Deliberately retained behavior

This is not a new direct-rendering sweep. Existing active behavior remains, including:

- shell-only copy polish after a shell revision
- configuration-guidance event scheduling and provider validation
- overview model-data lookup after overview rendering
- settings search focus scheduling
- Home Assistant-owned config-flow observation
- dirty-state/lifecycle microtasks and editor-deferred rendering
- real backend/lazy-loading asynchronous boundaries

Those paths have real ordering, external-DOM, or backend dependencies and are not removed merely because they are asynchronous.

## Regression coverage

Tests now verify that:

- the deleted bootstrap stays absent;
- direct management-panel dependencies remain registered as frontend assets;
- the former composition order is explicit in the panel;
- the affected modules contain no management installer sentinels or runtime replacements of panel render/confirm/configuration methods;
- confirmation guidance and configuration/search ownership remain wired after installer removal.
