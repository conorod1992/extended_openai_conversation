import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {spawnSync} from "node:child_process";
import test from "node:test";
import {fileURLToPath} from "node:url";

const frontend = new URL("../custom_components/extended_openai_conversation_responses/frontend/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("dist/manifest.json", frontend), "utf8"));
const shipped = Object.values(manifest).find(item => item.file.startsWith("assets/request-rules-ui-core-")).file;
for (const module of ["request-rules-ui-core.js", `dist/${shipped}`]) {
  test(`enabled patch excludes credential-bearing action data: ${module}`, () => {
    const result = spawnSync(process.execPath, [
      fileURLToPath(new URL("./fixtures/frontend/rule-enabled-wire.mjs", import.meta.url)),
      fileURLToPath(new URL(module, frontend)),
    ], {
      input: '{"rules":[{"id":"precision","enabled":true,"action":{"actions":[{"data":{"password":9007199254740993}}]}}]}',
      encoding: "utf8", timeout: 15000,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), {rule_id: "precision", rule: {enabled: false}});
  });
}
