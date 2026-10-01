// Structural execution evidence only: never persist errors, bodies or attachments.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {execFileSync} from "node:child_process";
import {fileURLToPath} from "node:url";
const sourceRoot = fileURLToPath(new URL("../", import.meta.url));

export default class ExecutionReporter {
  onBegin(config, suite) {
    this.root = config.rootDir;
    this.playwrightVersion = config.version;
    this.tests = suite.allTests();
  }
  onEnd(result) {
    if (process.env.ENHANCED_EXECUTION_EVIDENCE !== "1") return;
    let eoaiSha = "unknown";
    try {
      eoaiSha = execFileSync("git", ["-c", `safe.directory=${sourceRoot}`, "-C", sourceRoot, "rev-parse", "HEAD"],
        {encoding: "utf8", stdio: ["ignore", "pipe", "ignore"]}).trim();
    } catch {}
    const identity = {architecture: process.arch, node: process.versions.node,
      platform: process.platform, playwright: this.playwrightVersion};
    const fingerprint = crypto.createHash("sha256").update(JSON.stringify(identity)).digest("hex");
    const executionId = crypto.randomUUID();
    const cases = this.tests.map(test => ({
      nodeid: [path.relative(process.cwd(), test.location.file).replaceAll("\\", "/"),
        ...test.titlePath().slice(1)].join("::"),
      collected: true,
      executed: test.results.some(item => item.status !== "skipped"),
      outcome: test.results.length === 0 ? "incomplete"
        : test.expectedStatus === "failed" && test.results.at(-1).status === "passed" ? "xpassed"
        : test.expectedStatus === "failed" && test.results.at(-1).status === "failed" ? "xfailed"
        : test.results.some(item => item.status === "failed" || item.status === "timedOut" || item.status === "interrupted") ? "failed"
        : test.results.at(-1).status,
    }));
    const root = process.env.STRESS_ARTIFACT_DIR || "stress-artifacts";
    fs.mkdirSync(root, {recursive: true});
    fs.writeFileSync(path.join(root, `execution-playwright-${executionId}.json`),
      JSON.stringify({eoai_sha: eoaiSha, execution_id: executionId, environment: identity, environment_fingerprint: fingerprint, execution_schema: "eoai-test-execution/v1", runner: "playwright", status: result.status, cases}, null, 2));
  }
}
