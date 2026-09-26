import base from "./playwright.config.mjs";
import {devices} from "@playwright/test";

// Curated engine-sensitive cases; the full Chromium endurance suite stays in
// playwright.stress.config.mjs. Keep this collection outside ordinary PR CI.
const engine = process.env.CROSS_BROWSER_ENGINE;
export default {
  ...base,
  testMatch: [
    "cross-browser-keyboard.stress.mjs",
    "accessibility-layout.stress.mjs",
    "enhanced-endurance.stress.mjs",
    "management-crud.spec.mjs",
    "frontend-resilience.spec.mjs",
    "management-lazy-ownership.spec.mjs",
    "stale-response-ordering.spec.mjs",
    "backend-reconnect.spec.mjs",
  ],
  grep: /keyboard-only|malformed browser-local|major management pages|large text and long names|one mounted panel survives a long seeded route journey|persistent memories support create|failed Memory save|server-rejected general configuration save|a cold lazy import failure|search into a cold configuration route|older backend response|mounted management panel recovers/,
  timeout: 180_000,
  workers: 1,
  projects: [
    ...(!engine || engine === "chromium" ? [{name: "chromium", use: {...devices["Desktop Chrome"]}}] : []),
    ...(!engine || engine === "firefox" ? [{name: "firefox", use: {...devices["Desktop Firefox"]}}] : []),
    ...(!engine || engine === "webkit" ? [{name: "webkit", use: {...devices["Desktop Safari"]}}] : []),
  ],
};
