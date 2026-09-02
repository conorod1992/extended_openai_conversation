# Extended OpenAI frontend source

This directory is the typed frontend foundation for new Extended OpenAI UI work.
It is intentionally additive: the existing JavaScript under
`custom_components/extended_openai_conversation_responses/frontend/` remains the
runtime source of truth until an individual screen is migrated in a later PR.

## Commands

```bash
npm install
npm run check
npm test
npm run build
```

`npm run build` creates `dist/extended-openai-ui.js`. The build output is ignored
for now because no production screen imports this bundle yet. A later migration PR
must wire the built asset into Home Assistant deliberately rather than changing the
currently shipped frontend as a side effect of this foundation work.

## Conventions

- New reusable components extend `ExtendedOpenAiElement`.
- Shared navigation/notification events come from `events.ts` rather than ad-hoc
  event names.
- Formatting visible to users belongs in `format.ts` so list/detail screens stay
  consistent.
- Components use Home Assistant CSS variables and standard custom elements; they do
  not depend on private Home Assistant frontend classes.
