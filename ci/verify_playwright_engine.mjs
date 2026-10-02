import process from "node:process";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const globalRoot = process.env.NPM_GLOBAL_ROOT;
if (!globalRoot) {
  throw new Error("NPM_GLOBAL_ROOT must point to npm's global module directory");
}
const playwrightPackage = require(`${globalRoot}/@playwright/test/package.json`);
const {chromium, firefox, webkit} = require(`${globalRoot}/@playwright/test`);

const [engineName, expectedVersion, expectedBrowserVersion] = process.argv.slice(2);
if (!engineName || !expectedVersion || !["firefox", "webkit", "chromium"].includes(engineName)) {
  throw new Error("usage: verify_playwright_engine.mjs <chromium|firefox|webkit> <version>");
}
if (playwrightPackage.version !== expectedVersion) {
  throw new Error(`Playwright ${playwrightPackage.version} installed; expected ${expectedVersion}`);
}

const engines = {chromium, firefox, webkit};
const browser = await engines[engineName].launch({headless: true});
try {
  const browserVersion = browser.version();
  if (expectedBrowserVersion && browserVersion !== expectedBrowserVersion) {
    throw new Error(`Browser ${browserVersion} installed; expected ${expectedBrowserVersion}`);
  }
  console.log(JSON.stringify({engine: engineName, playwright: playwrightPackage.version, browser: browserVersion}));
} finally {
  await browser.close();
}
