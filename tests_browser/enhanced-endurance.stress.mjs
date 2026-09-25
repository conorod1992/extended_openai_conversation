import {expect, test} from "@playwright/test";
import {mkdirSync, writeFileSync} from "node:fs";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const routes = [
  "assistant/basics", "assistant/conversation", "assistant/voice",
  "capabilities/home-assistant", "capabilities/request-rules", "capabilities/functions",
  "capabilities/guest-mode", "capabilities/quiet-hours", "data-memory/memories",
  "data-memory/knowledge", "usage-maintenance/usage", "usage-maintenance/backup-restore",
];

function seededRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

test("one mounted panel survives a long seeded route journey", async ({page}, testInfo) => {
  test.setTimeout(180_000);
  const seed = Number(process.env.STRESS_SEED || 237101);
  const count = process.env.STRESS_INTENSITY === "heavy" ? 320 : 80;
  const random = seededRandom(seed ^ 0xB20E);
  const operations = [];
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await panel.evaluate((host, value) => { host.__nightlyMount = value; }, seed);
  let maxNodes = 0;
  try {
    for (let index = 0; index < count; index++) {
      const route = routes[Math.floor(random() * routes.length)];
      operations.push({number: index + 1, operation: "navigate", route});
      await page.evaluate((nextRoute) => {
        history.pushState({}, "", `/extended-openai/${nextRoute}`);
        window.browserHarness.panel.route = {};
      }, route);
      await expect(page).toHaveURL(new RegExp(`/extended-openai/${route}$`));
      await expect(panel.locator("#agent")).toHaveValue("agent-1");
      await expect(panel).toHaveCount(1);
      expect(await panel.evaluate((host) => host.__nightlyMount)).toBe(seed);
      const nodes = await panel.evaluate((host) => host.shadowRoot.querySelectorAll("*").length);
      maxNodes = Math.max(nodes, maxNodes);
      expect(nodes).toBeLessThan(6000);
      if (index % 12 === 0) await expect(panel.getByRole("alert")).toHaveCount(0);
    }
    await expectHarnessClean(page, errors);
  } finally {
    const report = {seed, test: testInfo.title, count, maxNodes, operations};
    mkdirSync(process.env.STRESS_ARTIFACT_DIR || "stress-artifacts", {recursive: true});
    writeFileSync(`${process.env.STRESS_ARTIFACT_DIR || "stress-artifacts"}/browser-endurance.json`, JSON.stringify(report, null, 2));
    await testInfo.attach("endurance-operations", {body: JSON.stringify(report, null, 2), contentType: "application/json"});
    console.log(`ENHANCED BROWSER seed=${seed} transitions=${count} max_dom_nodes=${maxNodes}`);
  }
});
