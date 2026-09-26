import base from "./playwright.config.mjs";

// Only the explicit nightly/manual artifact self-test uses this config.
export default {...base, testMatch: /nightly-diagnostics-probe\.mjs$/};
