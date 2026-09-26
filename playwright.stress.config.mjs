import base from "./playwright.config.mjs";
import {devices} from "@playwright/test";

// Explicit nightly collection: ordinary frontend CI must never pick this file up.
export default {
  ...base,
  testMatch: /.*\.stress\.mjs$/,
  timeout: 180_000,
  workers: 1,
  projects: process.env.CROSS_BROWSER_ENGINE
    ? [{name: process.env.CROSS_BROWSER_ENGINE, use: {
      ...devices[process.env.CROSS_BROWSER_ENGINE === "firefox" ? "Desktop Firefox" : "Desktop Safari"],
    }}]
    : base.projects,
};
