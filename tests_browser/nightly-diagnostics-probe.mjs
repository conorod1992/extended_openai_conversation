import {execFileSync} from "node:child_process";
import {expect, test} from "@playwright/test";

// Invoked explicitly by the diagnostics campaign; excluded from ordinary suites.
test("controlled browser failure retains trace and screenshot", async ({page}, testInfo) => {
  await page.goto("/tests_browser/fixture.html");
  const python = process.platform === "win32" ? "python" : "python3";
  const probes = JSON.parse(execFileSync(python, ["-c", "import json, os; from ci.enhanced_evidence import fresh_privacy_canaries; print(json.dumps(fresh_privacy_canaries(os.environ.get('STRESS_SEED', 'unknown'))))"], {encoding: "utf8"}));
  const sanitized = execFileSync(python, ["-c", "import json,sys; from ci.enhanced_evidence import safe; print(json.dumps(safe(json.load(sys.stdin))))"], {input: JSON.stringify({boundary: "fixture-browser", seed: process.env.STRESS_SEED || "unknown", diagnosticMarker: "USEFUL-PRIVACY-DIAGNOSTIC", operations: ["open", "assert"], api_key: probes[0], authorization: probes[1], user_text: probes[2], prompt: probes[3]}), encoding: "utf8"});
  await testInfo.attach("operation-trace", {
    body: sanitized,
    contentType: "application/json",
  });
  await expect(page.locator("#deliberately-missing-diagnostics-node"), `USEFUL-PRIVACY-DIAGNOSTIC ${probes[0]} ${probes[1]}`).toBeVisible({timeout: 250});
});
