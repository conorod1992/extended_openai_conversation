import {expect, test} from "@playwright/test";
import {mkdirSync, writeFileSync} from "node:fs";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("one mounted panel has bounded backend calls after repeated disconnects", async ({page}, testInfo) => {
  test.setTimeout(180_000);
  const cycles = process.env.STRESS_INTENSITY === "heavy" ? 80 : 20;
  const errors = trackPageErrors(page);
  const operations = [];
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
  await page.evaluate(() => {
    const harness = window.browserHarness;
    harness.backendOnline = true;
    harness.disconnectAttempts = 0;
    const original = harness.hass.callWS.bind(harness.hass);
    harness.hass.callWS = async message => {
      if (!harness.backendOnline) {
        harness.disconnectAttempts++;
        throw new Error("Home Assistant backend disconnected");
      }
      return original(message);
    };
    harness.panel.__reconnectMount = "original";
  });

  let baselineCalls = null;
  let mutationCycles = 0;
  let marker = null;
  try {
    for (let index = 0; index < cycles; index++) {
      await page.evaluate(() => { window.browserHarness.backendOnline = false; });
      await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();
      await expect(page).toHaveURL(/\/extended-openai\/usage-maintenance\/usage$/);
      await expect(panel.getByText("Usage summary unavailable", {exact: true})).toBeVisible();
      await page.evaluate(() => { window.browserHarness.backendOnline = true; });
      await panel.locator('.top-nav button[data-page="data-memory"]').click();
      await expect(page).toHaveURL(/\/extended-openai\/data-memory\//);
      await page.evaluate(() => {
        history.pushState({}, "", "/extended-openai/data-memory/knowledge");
        window.browserHarness.panel.route = {};
      });
      await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();
      const before = await page.evaluate(() => window.browserHarness.calls.length);
      await panel.evaluate(async host => { await host._loadSection(true); });
      const observed = await page.evaluate(start => window.browserHarness.calls.length - start, before);
      if (baselineCalls === null) baselineCalls = observed;
      expect(observed).toBeLessThanOrEqual(baselineCalls + 2);
      if (index % 4 === 0) {
        const mode = marker === null ? "create" : (mutationCycles % 3 === 1 ? "edit" : "delete");
        const previousCalls = await page.evaluate(() => window.browserHarness.calls.length);
        if (mode === "create") {
          marker = `Reconnect source ${index}`;
          await panel.locator("#add-source").click();
          await panel.locator("#knowledge-title").fill(marker);
          await panel.locator("#knowledge-content").fill(`Content ${index}`);
          await panel.locator("#knowledge-save").click();
        } else if (mode === "edit") {
          await panel.locator(".list-card").filter({hasText: marker}).locator(".source-edit-button").click();
          const updated = `${marker} edited`;
          await panel.locator("#knowledge-title").fill(updated);
          await panel.locator("#knowledge-save").click();
          marker = updated;
        } else {
          await panel.locator(".list-card").filter({hasText: marker}).locator(".delete-source").click();
          await acceptConfirmation(panel);
          marker = null;
        }
        const mutations = await page.evaluate(start => window.browserHarness.calls.slice(start).filter(call => call.section === "knowledge" && ["create", "update", "delete"].includes(call.action)).map(call => call.action), previousCalls);
        expect(mutations).toHaveLength(1);
        const matches = await page.evaluate(value => window.browserHarness.getState().knowledgeSources.filter(source => source.title === value).length, marker);
        expect(matches).toBe(marker === null ? 0 : 1);
        mutationCycles++;
        operations.push({cycle: index + 1, mutation: mode, backendMutations: mutations.length});
      }
      await expect(panel).toHaveCount(1);
      expect(await panel.evaluate(host => host.__reconnectMount)).toBe("original");
      await expect(panel.getByRole("alert")).toHaveCount(0);
      operations.push({cycle: index + 1, backendCalls: observed});
    }
    expect(await page.evaluate(() => window.browserHarness.disconnectAttempts)).toBeGreaterThanOrEqual(cycles);
    await expectHarnessClean(page, errors);
  } finally {
    const report = {cycles, baselineCalls, mutationCycles, operations};
    mkdirSync(process.env.STRESS_ARTIFACT_DIR || "stress-artifacts", {recursive: true});
    writeFileSync(`${process.env.STRESS_ARTIFACT_DIR || "stress-artifacts"}/browser-reconnect.json`, JSON.stringify(report, null, 2));
    await testInfo.attach("reconnect-operations", {body: JSON.stringify(report, null, 2), contentType: "application/json"});
    console.log(`ENHANCED RECONNECT cycles=${cycles} mutation_cycles=${mutationCycles} baseline_backend_calls=${baselineCalls}`);
  }
});
