import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) {
  test(`All available shows recorded dates ahead of HA's current day (${bundled ? "shipped" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.clock.install({time:new Date("2026-09-13T02:00:00Z")});
    await page.goto(fixtureUrl("overview", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".dashboard-grid")).toBeVisible();
    await panel.evaluate(async host => {
      const original = host._hass.callWS.bind(host._hass);
      host._hass.config.time_zone = "America/New_York";
      host.__dailyUsageBounds = [];
      host._hass.callWS = async message => {
        if (message.section !== "usage") return original(message);
        if (message.action === "summary") return {today:{date:"2026-09-12"}};
        if (message.action === "runs") return {runs:[]};
        if (message.action === "daily") {
          const {start_date, end_date} = message;
          host.__dailyUsageBounds.push([start_date, end_date]);
          // This run was recorded on September 13 before HA moved west.
          const day = "2026-09-13";
          const included = start_date <= day && day <= end_date;
          return {
            days:included ? [{date:day, run_count:1, successful_run_count:1}] : [],
            has_more:false,
            local_run_summary:{count:Number(included), start_date, end_date, retention_days:30},
          };
        }
        return original(message);
      };
      await host._navigate("usage-maintenance", "usage");
    });

    const selector = panel.getByLabel("History window");
    const range = panel.locator(".usage-range-copy small");
    const requests = panel.locator(".metric").filter({hasText:"Provider requests"});
    const retained = panel.locator(".usage-fact").filter({hasText:"Retained runs without provider requests"});
    await expect(range).toHaveText("30 days · 2026-08-14 to 2026-09-12");
    await expect(requests).toContainText("0 conversation runs");

    await selector.selectOption("all");
    await expect(range).toHaveText("All available · 2026-09-13 to 2026-09-13");
    await expect(requests).toContainText("1 conversation runs");
    await expect(retained.locator("strong")).toHaveText("1");
    await expect(panel.locator(".chart-column")).toHaveCount(1);
    await expect(panel.locator(".usage-history-note")).not.toContainText("No daily usage history yet");

    await selector.selectOption("year");
    await expect(range).toHaveText("Year to date · 2026-01-01 to 2026-09-12");
    await expect(requests).toContainText("0 conversation runs");
    await expect(retained.locator("strong")).toHaveText("0");

    await panel.evaluate(host => { host._hass.config.time_zone = "UTC"; });
    await selector.selectOption("all");
    await expect(requests).toContainText("1 conversation runs");
    await expect(retained.locator("strong")).toHaveText("1");
    expect(await panel.evaluate(host => host.__dailyUsageBounds)).toEqual([
      ["2026-08-14", "2026-09-12"],
      ["0000-01-01", "9999-12-31"],
      ["2026-01-01", "2026-09-12"],
    ]);
    await expectHarnessClean(page, errors);
  });
}
