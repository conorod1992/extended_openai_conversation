import {readdirSync, readFileSync, writeFileSync} from "node:fs";
import {gzipSync} from "node:zlib";

const assets = new URL("../custom_components/extended_openai_conversation_responses/frontend/dist/assets/", import.meta.url);
for (const name of readdirSync(assets)) {
  if (!/\.(?:js|css)$/.test(name)) continue;
  const compressed = gzipSync(readFileSync(new URL(name, assets)), {level: 9});
  // Gzip's OS header must be identical on the CI and developer hosts.
  compressed[9] = 255;
  writeFileSync(new URL(`${name}.gz`, assets), compressed);
}
