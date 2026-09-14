import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("agent picker stays stable while an agent-scoped mutation is in flight", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));

  const panel = page.locator("extended-openai-management-panel");
  const agentPicker = panel.locator("#agent");
  await expect(agentPicker).toBeVisible();

  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    panel._data.agents.push({
      ...panel._data.agents[0],
      subentry_id: "agent-2",
      title: "Friday",
    });
    panel._render();

    const originalCallWS = window.browserHarness.hass.callWS.bind(window.browserHarness.hass);
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    window.browserHarness.releaseMemoryMutation = release;
    window.browserHarness.memoryMutationStarted = false;
    window.browserHarness.hass.callWS = async (message) => {
      if (message.section === "memories" && message.action === "add") {
        window.browserHarness.memoryMutationStarted = true;
        await gate;
      }
      return originalCallWS(message);
    };
  });

  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Pending Agent A memory");
  await panel.locator("#memory-save").click();

  await expect.poll(async () => page.evaluate(() => window.browserHarness.memoryMutationStarted)).toBe(true);
  await expect(agentPicker).toBeDisabled();
  await expect(agentPicker).toHaveValue("agent-1");

  await agentPicker.selectOption("agent-2", {force: true});
  await expect(agentPicker).toHaveValue("agent-1");

  await page.evaluate(() => window.browserHarness.releaseMemoryMutation());
  await expect(agentPicker).toBeEnabled();

  await agentPicker.selectOption("agent-2");
  await expect(agentPicker).toHaveValue("agent-2");
  await expectHarnessClean(page, pageErrors);
});
