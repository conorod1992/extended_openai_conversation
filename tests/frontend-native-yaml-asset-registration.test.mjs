import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const manifestPath = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/dist/manifest.json",
  import.meta.url,
);
const loaderPath = new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/management-route.js",
  import.meta.url,
);

const [manifestText, loaderSource] = await Promise.all([
  readFile(manifestPath, "utf8"),
  readFile(loaderPath, "utf8"),
]);
const manifest = JSON.parse(manifestText);

assert.match(
  loaderSource,
  /import\("\.\/agent-config-tools\.js"\)/,
  "the Functions route must lazy-load the tools editor with native YAML bindings",
);
const nativeYaml = Object.entries(manifest).find(([source]) =>
  source.endsWith("/agent-config-tools.js")
);
assert.ok(nativeYaml, "the lazy Function Tools feature must be emitted in the production manifest");
assert.equal(nativeYaml[1].isDynamicEntry, true);
