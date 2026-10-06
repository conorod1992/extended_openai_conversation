import base from "./playwright.config.mjs";
import {devices} from "@playwright/test";

// Reuse compact interaction journeys rather than maintaining nightly copies.
// Heavy campaigns remain exclusive to *.stress.mjs; these existing smoke
// journeys also close the nightly interaction inventory at negligible cost.
export const nightlyInteractionSpecs = [
  "overview-broadcast-lazy.spec.mjs",
  "management-crud.spec.mjs",
  "native-yaml-editor-crud.spec.mjs",
  "conversation-actions.spec.mjs",
  "request-debug-persistence.spec.mjs",
  "timezone-locale-boundaries.spec.mjs",
  "control-reachability.spec.mjs",
];
export default {
  ...base,
  testMatch: [/.*\.stress\.mjs$/, ...nightlyInteractionSpecs],
  timeout: 180_000,
  workers: 1,
  projects: process.env.CROSS_BROWSER_ENGINE
    ? [{name: process.env.CROSS_BROWSER_ENGINE, use: {
      ...devices[process.env.CROSS_BROWSER_ENGINE === "firefox" ? "Desktop Firefox" : "Desktop Safari"],
    }}]
    : base.projects,
};
