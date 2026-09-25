import base from "./playwright.config.mjs";

// Explicit nightly collection: ordinary frontend CI must never pick this file up.
export default {
  ...base,
  testMatch: /.*\.stress\.mjs$/,
  timeout: 180_000,
  workers: 1,
};
