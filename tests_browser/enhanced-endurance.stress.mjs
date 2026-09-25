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
  let mutations = 0;
  try {
    for (let index = 0; index < count; index++) {
      const action = index % 5 === 0 ? ["memory", "knowledge", "rule"][Math.floor(random() * 3)] : null;
      const route = action === "memory" ? "data-memory/memories" : action === "knowledge" ? "data-memory/knowledge" : action === "rule" ? "capabilities/request-rules" : routes[Math.floor(random() * routes.length)];
      operations.push({number: index + 1, operation: "navigate", route});
      await page.evaluate((nextRoute) => {
        history.pushState({}, "", `/extended-openai/${nextRoute}`);
        window.browserHarness.panel.route = {};
      }, route);
      await expect(page).toHaveURL(new RegExp(`/extended-openai/${route}$`));
      await expect(panel.locator("#agent")).toHaveValue("agent-1");
      if (action === "memory") {
        const marker = `Endurance memory ${seed}-${index}`;
        await panel.locator("#add-memory").click();
        await panel.locator("#memory-content").fill(marker);
        await panel.locator("#memory-category").fill("endurance");
        await panel.locator("#memory-save").click();
        await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);
        await expect(panel.getByText(marker, {exact: true})).toBeVisible();
        const stored = await page.evaluate((value) => window.browserHarness.getState().memories.filter((item) => item.content === value).length, marker);
        expect(stored).toBe(1);
        operations.push({number: index + 1, operation: "memory_create", marker});
        mutations++;
      } else if (action === "knowledge") {
        const marker = `Endurance source ${seed}-${index}`;
        await panel.locator("#add-source").click();
        await panel.locator("#knowledge-title").fill(marker);
        await panel.locator("#knowledge-content").fill(`Content for ${marker}`);
        await panel.locator("#knowledge-save").click();
        await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
        await expect(panel.getByText(marker, {exact: true})).toBeVisible();
        const stored = await page.evaluate((value) => window.browserHarness.getState().knowledgeSources.filter((item) => item.title === value).length, marker);
        expect(stored).toBe(1);
        operations.push({number: index + 1, operation: "knowledge_create", marker});
        mutations++;
      } else if (action === "rule") {
        const marker = `Endurance rule ${seed}-${index}`;
        await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
        await panel.locator("#rule-name").fill(marker);
        await panel.locator("#rule-phrases").fill(`endurance phrase ${seed} ${index}`);
        await panel.locator("#rule-action-type").selectOption("model_routing");
        await panel.locator("#rule-model").fill("gpt-5-mini");
        await panel.locator("#rule-save").click();
        await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
        await expect(panel.getByRole("heading", {name: marker, exact: true})).toBeVisible();
        const stored = await page.evaluate((value) => window.browserHarness.getState().requestRules.rules.filter((item) => item.name === value).length, marker);
        expect(stored).toBe(1);
        operations.push({number: index + 1, operation: "rule_create", marker});
        mutations++;
      }
      await expect(panel).toHaveCount(1);
      expect(await panel.evaluate((host) => host.__nightlyMount)).toBe(seed);
      const nodes = await panel.evaluate((host) => host.shadowRoot.querySelectorAll("*").length);
      maxNodes = Math.max(nodes, maxNodes);
      expect(nodes).toBeLessThan(6000);
      if (index % 12 === 0) await expect(panel.getByRole("alert")).toHaveCount(0);
    }
    await expectHarnessClean(page, errors);
  } finally {
    const report = {seed, test: testInfo.title, count, mutations, maxNodes, operations};
    mkdirSync(process.env.STRESS_ARTIFACT_DIR || "stress-artifacts", {recursive: true});
    writeFileSync(`${process.env.STRESS_ARTIFACT_DIR || "stress-artifacts"}/browser-endurance.json`, JSON.stringify(report, null, 2));
    await testInfo.attach("endurance-operations", {body: JSON.stringify(report, null, 2), contentType: "application/json"});
    console.log(`ENHANCED BROWSER seed=${seed} transitions=${count} mutations=${mutations} max_dom_nodes=${maxNodes}`);
  }
});
