import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import Reporter from "../ci/playwright_execution.mjs";

test("execution reporter preserves skipped, failed, xfail and missing browser execution without private diagnostics", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "eoai-execution-"));
  const oldRoot = process.env.STRESS_ARTIFACT_DIR;
  const oldEnabled = process.env.ENHANCED_EXECUTION_EVIDENCE;
  process.env.STRESS_ARTIFACT_DIR = root;
  process.env.ENHANCED_EXECUTION_EVIDENCE = "1";
  try {
    const scenarios = [
      ["healthy", "passed", "passed", true],
      ["no-browser-prerequisite", "skipped", "skipped", false],
      ["xfail", "failed", "xfailed", true],
      ["xfail-skipped", "skipped", "skipped", false],
      ["xfail-timeout", "timedOut", "failed", true],
      ["xpass", "passed", "xpassed", true],
      ["broken", "failed", "failed", true],
      ["lost", null, "incomplete", false],
    ];
    const reporter = new Reporter();
    reporter.onBegin({rootDir: process.cwd()}, {allTests: () => scenarios.map(([title, status]) => ({
      location: {file: path.join(process.cwd(), "tests_browser", "probe.spec.mjs")},
      titlePath: () => ["", "chromium", "probe.spec.mjs", title],
      expectedStatus: title.startsWith("xfail") || title === "xpass" ? "failed" : "passed",
      results: status ? [{status, errors: [{message: "PRIVATE-MEMORY-CANARY-8274"}]}] : [],
    }))});
    reporter.onEnd({status: "failed"});
    const raw = fs.readFileSync(path.join(root, fs.readdirSync(root)[0]), "utf8");
    const ledger = JSON.parse(raw);
    assert.equal(ledger.runner, "playwright");
    assert.match(ledger.eoai_sha, /^[0-9a-f]{40}$/);
    assert.ok(ledger.execution_id);
    assert.match(ledger.environment.playwright, /^\d+\.\d+\.\d+/);
    assert.match(ledger.environment_fingerprint, /^[0-9a-f]{64}$/);
    assert.deepEqual(ledger.cases.map(item => [item.nodeid.split("::").at(-1), item.outcome, item.executed]),
      scenarios.map(([title, _status, outcome, executed]) => [title, outcome, executed]));
    assert.ok(ledger.cases.every(item => item.collected));
    assert.ok(!raw.includes("PRIVATE-MEMORY-CANARY-8274"));
  } finally {
    if (oldRoot === undefined) delete process.env.STRESS_ARTIFACT_DIR;
    else process.env.STRESS_ARTIFACT_DIR = oldRoot;
    if (oldEnabled === undefined) delete process.env.ENHANCED_EXECUTION_EVIDENCE;
    else process.env.ENHANCED_EXECUTION_EVIDENCE = oldEnabled;
    fs.rmSync(root, {recursive: true, force: true});
  }
});
