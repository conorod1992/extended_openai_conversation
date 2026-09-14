import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("shared browser diagnostics tracker captures console and network failures", async ({page}) => {
  const diagnostics = trackPageErrors(page);
  await page.goto(fixtureUrl("overview"));

  await page.route("**/tripwire-abort", (route) => route.abort("failed"));
  await page.evaluate(async () => {
    console.error("browser diagnostics tripwire console error");
    await fetch("/tests_browser/definitely-missing-tripwire").catch(() => {});
    await fetch("/tripwire-abort").catch(() => {});
  });

  expect(diagnostics.consoleErrors.some((item) => item.includes("browser diagnostics tripwire console error"))).toBe(true);
  expect(diagnostics.badResponses.some((item) => item.includes("definitely-missing-tripwire") && item.startsWith("404 "))).toBe(true);
  expect(diagnostics.requestFailures.some((item) => item.includes("tripwire-abort"))).toBe(true);
});
