import {expect, test} from "@playwright/test";

import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const route = "capabilities/scheduled-actions";

async function openScheduledActions(page) {
  await page.goto(fixtureUrl("overview", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("combobox", {name: "Editing assistant"})).toBeVisible();
  await page.evaluate(() => {
    const {hass, panel} = browserHarness;
    const original = hass.callWS;
    const primary = panel._agentId;
    let release;
    const oldRead = new Promise(resolve => { release = resolve; });
    let releaseCancel;
    const oldCancel = new Promise(resolve => { releaseCancel = resolve; });
    const first = {action_id: "1".repeat(32), description: "Primary lights", scheduled_at: "2026-10-25T00:30:00+00:00"};
    const second = {action_id: "2".repeat(32), description: "Second assistant lights", scheduled_at: "2026-10-25T01:30:00+00:00"};
    window.scheduledFixture = {primary, release, hold: false, oldReadStarted: false, cancelStatus: "cancelled", lists: 0};
    const fixture = window.scheduledFixture;
    fixture.releaseCancel = releaseCancel;
    const byAssistant = {[primary]: [first], "scale-agent-1": [second]};
    hass.callWS = async request => {
      if (request.section !== "scheduled_actions") return original(request);
      if (request.action === "list") {
        fixture.lists++;
        const actions = structuredClone(byAssistant[request.subentry_id] || []);
        if (fixture.hold && request.subentry_id === primary) {
          fixture.oldReadStarted = true;
          await oldRead;
        }
        if (fixture.unavailable) return {status: "unavailable"};
        return {status: "ok", actions};
      }
      const {action_id, subentry_id} = request;
      if (fixture.cancelStatus === "uncertain") throw new Error("reply lost");
      if (fixture.cancelStatus === "not_pending") {
        byAssistant[subentry_id] = [];
        return {status: "not_pending"};
      }
      byAssistant[subentry_id] = byAssistant[subentry_id].filter(action => action.action_id !== action_id);
      if (fixture.holdCancel) {
        fixture.cancelStarted = true;
        await oldCancel;
        fixture.cancelFinished = true;
      }
      return {status: "cancelled", action_id};
    };
    history.pushState({}, "", "/extended-openai/capabilities/scheduled-actions");
    panel.route = {};
  });
  await expect(panel.getByRole("heading", {name: "Scheduled Actions"})).toBeVisible();
  return panel;
}

test("Scheduled Actions cancellation survives refresh for the selected assistant", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openScheduledActions(page);
  await expect(panel.getByRole("heading", {name: "Primary lights"})).toBeVisible();
  await expect(panel.locator("time")).toContainText("1:30");
  await panel.getByRole("button", {name: "Refresh", exact: true}).click();
  await expect.poll(() => page.evaluate(() => scheduledFixture.lists)).toBe(2);
  await panel.getByRole("button", {name: /Cancel Primary lights/}).click();
  await expect(panel.getByText("Scheduled action cancelled", {exact:true})).toBeVisible();
  await expect(panel.getByText("No pending actions for this assistant.")).toBeVisible();
  await expect.poll(() => page.evaluate(() => scheduledFixture.lists)).toBe(3);
  await expectHarnessClean(page, errors);
});

test("Scheduled Actions ignores a late read from a previously selected assistant", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openScheduledActions(page);
  await page.evaluate(() => { scheduledFixture.hold = true; });
  await panel.getByRole("button", {name: "Refresh", exact: true}).click();
  await expect.poll(() => page.evaluate(() => scheduledFixture.oldReadStarted)).toBe(true);
  await panel.getByRole("combobox", {name: "Editing assistant"}).selectOption("scale-agent-1");
  await expect(panel.getByRole("heading", {name: "Second assistant lights"})).toBeVisible();
  await page.evaluate(() => { scheduledFixture.release(); });
  await expect(panel.getByRole("heading", {name: "Primary lights"})).toHaveCount(0);
  await expect(panel.getByRole("heading", {name: "Second assistant lights"})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("Scheduled Actions ignores a cancellation reply after switching assistants", async ({page}) => {
  const panel = await openScheduledActions(page);
  await page.evaluate(() => { scheduledFixture.holdCancel = true; });
  await panel.getByRole("button", {name: /Cancel Primary lights/}).click();
  await expect.poll(() => page.evaluate(() => scheduledFixture.cancelStarted)).toBe(true);
  await panel.getByRole("combobox", {name: "Editing assistant"}).selectOption("scale-agent-1");
  await expect(panel.getByRole("heading", {name: "Second assistant lights"})).toBeVisible();
  await page.evaluate(() => { scheduledFixture.releaseCancel(); });
  await expect.poll(() => page.evaluate(() => scheduledFixture.cancelFinished)).toBe(true);
  await expect(panel.getByRole("heading", {name: "Second assistant lights"})).toBeVisible();
  await expect(panel.getByText("Scheduled action cancelled", {exact:true})).toHaveCount(0);
});

test("Scheduled Actions refresh after an unconfirmed or already settled cancellation", async ({page}) => {
  const panel = await openScheduledActions(page);
  await page.evaluate(() => { scheduledFixture.cancelStatus = "uncertain"; });
  await panel.getByRole("button", {name: /Cancel Primary lights/}).click();
  await expect(panel.getByText("Cancellation could not be confirmed.")).toBeVisible();
  await expect.poll(() => page.evaluate(() => scheduledFixture.lists)).toBe(2);
  await page.evaluate(() => { scheduledFixture.cancelStatus = "not_pending"; });
  await panel.getByRole("button", {name: /Cancel Primary lights/}).click();
  await expect(panel.getByText("This action is no longer pending.")).toBeVisible();
  await expect(panel.getByText("No pending actions for this assistant.")).toBeVisible();
  await page.evaluate(() => { scheduledFixture.unavailable = true; });
  await panel.getByRole("button", {name: "Refresh", exact: true}).click();
  await expect(panel.getByText("The scheduled action queue is unavailable.")).toBeVisible();
});
