// Structural execution evidence only: never persist errors, bodies or attachments.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

export default class ExecutionReporter {
  onBegin(config, suite) {
    this.root = config.rootDir;
    this.tests = suite.allTests();
  }
  onEnd(result) {
    if (process.env.ENHANCED_EXECUTION_EVIDENCE !== "1") return;
    const cases = this.tests.map(test => ({
      nodeid: [path.relative(process.cwd(), test.location.file).replaceAll("\\", "/"),
        ...test.titlePath().slice(1)].join("::"),
      collected: true,
      executed: test.results.some(item => item.status !== "skipped"),
      outcome: test.results.length === 0 ? "incomplete"
        : test.expectedStatus === "failed" ? (test.results.at(-1).status === "passed" ? "xpassed" : "xfailed")
        : test.results.some(item => item.status === "failed" || item.status === "timedOut" || item.status === "interrupted") ? "failed"
        : test.results.at(-1).status,
    }));
    const root = process.env.STRESS_ARTIFACT_DIR || "stress-artifacts";
    fs.mkdirSync(root, {recursive: true});
    fs.writeFileSync(path.join(root, `execution-playwright-${crypto.randomUUID()}.json`),
      JSON.stringify({execution_schema: "eoai-test-execution/v1", runner: "playwright", status: result.status, cases}, null, 2));
  }
}
