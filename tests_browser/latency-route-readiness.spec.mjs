import {expect, test} from "@playwright/test";
import {LATENCY_ROUTES, waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const route of LATENCY_ROUTES) {
  test(`latency readiness contract: ${route.name}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(route.path));
    await expect(page.locator("extended-openai-management-panel")).toHaveCount(1);
    await waitForManagementRouteReady(page, route, 7500);
    await expectHarnessClean(page, errors);
  });
}
