import {readFileSync, readdirSync} from "node:fs";
import {createHash} from "node:crypto";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {resolve} from "node:path";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const source = "custom_components/extended_openai_conversation_responses/frontend";
const dist = `${source}/dist/`;
const distPath = resolve(root, dist);
const distDigest = () => {
  const hash = createHash("sha256");
  const visit = (folder, prefix = "") => {
    for (const entry of readdirSync(folder, {withFileTypes:true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const relative = `${prefix}${entry.name}`;
      if (entry.isDirectory()) visit(resolve(folder, entry.name), `${relative}/`);
      else if (entry.isFile()) {
        hash.update(relative);
        hash.update(readFileSync(resolve(folder, entry.name)));
      }
    }
  };
  visit(distPath);
  return hash.digest("hex");
};
const run = (command, args, cwd = root) => {
  console.log(`\n> ${command} ${args.join(" ")}`);
  const useCmd = process.platform === "win32" && (command === "npm" || command === "pnpm");
  const result = useCmd
    ? spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", `${command} ${args.join(" ")}`], {cwd, stdio:"inherit"})
    : spawnSync(command, args, {cwd, stdio:"inherit"});
  if (result.error || result.status !== 0) {
    console.error(result.error?.message || `${command} exited with status ${result.status}`);
    process.exit(1);
  }
};

for (const file of readdirSync(resolve(root, source)).filter((name) => name.endsWith(".js")).sort()) {
  run(process.execPath, ["--check", `${source}/${file}`]);
}
run(process.execPath, ["scripts/run-frontend-standalone-tests.mjs"]);

const available = (name) => spawnSync(process.platform === "win32" ? "where.exe" : "which", [name], {stdio:"ignore"}).status === 0;
const manager = available("npm") ? "npm" : available("pnpm") ? "pnpm" : null;
if (!manager) throw new Error("Install npm or pnpm to run frontend checks and build.");
for (const script of ["check", "test", "build"]) run(manager, ["run", script], resolve(root, "frontend"));
const builtDigest = distDigest();
run(manager, ["run", "build"], resolve(root, "frontend"));
if (distDigest() !== builtDigest) throw new Error("Frontend build changed dist/ on a second run.");

console.log("\nTracked dist/ was rebuilt and verified stable on a second build.");
run("git", ["status", "--short", "--", dist]);
console.log("Commit any dist/ changes shown above. CI independently checks the committed build with git diff --exit-code.");
