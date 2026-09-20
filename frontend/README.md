# Extended OpenAI frontend tooling

The shipped frontend source lives under
`custom_components/extended_openai_conversation_responses/frontend/`.

This `frontend/` package provides the TypeScript/Vitest/Vite toolchain used to
type-check frontend tests and build the production management bundle.

## Commands

```bash
npm ci
npm run check
npm test
npm run build
```

`npm run build` writes the tracked production bundle under
`custom_components/extended_openai_conversation_responses/frontend/dist/`.
The Vite entry point is the production `management-panel.js` module.
