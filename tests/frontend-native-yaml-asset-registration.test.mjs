import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const initPath = new URL(
  "../custom_components/extended_openai_conversation_responses/__init__.py",
  import.meta.url,
);
const loaderPath = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/agent-config-loader.js",
  import.meta.url,
);

const [initSource, loaderSource] = await Promise.all([
  readFile(initPath, "utf8"),
  readFile(loaderPath, "utf8"),
]);

assert.match(
  loaderSource,
  /import\("\.\/agent-config-native-yaml\.js"\)/,
  "the agent configuration loader must lazy-load the native YAML wrapper",
);
assert.match(
  initSource,
  /"agent-config-native-yaml\.js"/,
  "every lazily imported management frontend module must be registered as a Home Assistant static asset",
);
