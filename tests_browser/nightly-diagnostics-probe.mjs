import {expect, test} from "@playwright/test";

// Invoked explicitly by the diagnostics campaign; excluded from ordinary suites.
test("controlled browser failure retains trace and screenshot", async ({page}, testInfo) => {
  await page.goto("/tests_browser/fixture.html");
  await testInfo.attach("operation-trace", {
    body: JSON.stringify({boundary: "fixture-browser", operations: ["open", "assert"]}),
    contentType: "application/json",
  });
  await expect(page.locator("#deliberately-missing-diagnostics-node")).toBeVisible({timeout: 250});
});
