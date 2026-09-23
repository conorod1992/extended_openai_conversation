import {readdirSync} from "node:fs";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {resolve} from "node:path";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const tests = readdirSync(resolve(root, "tests"), {withFileTypes:true})
  .filter((entry) => entry.isFile() && entry.name.endsWith(".test.mjs"))
  .map((entry) => `tests/${entry.name}`)
  .sort();
const failed = [];
for (const file of tests) {
  const result = spawnSync(process.execPath, [file], {cwd:root, stdio:"inherit"});
  if (result.status !== 0 || result.error) {
    failed.push(file);
    console.error(`FAILED: ${file}${result.error ? ` (${result.error.message})` : ""}`);
  }
}
if (failed.length) {
  console.error(`\n${failed.length} of ${tests.length} standalone frontend tests failed: ${failed.join(", ")}`);
  process.exitCode = 1;
} else {
  console.log(`\n${tests.length} standalone frontend tests passed.`);
}
