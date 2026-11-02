import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("an uncertain new-agent import retains edited source and requires an accepted catalogue read before another preview", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
  await panel.evaluate(host => {
    const send = host.hass.callWS.bind(host.hass);
    window.importRecoveryState = {created:[], previews:0, reads:0, failRead:true};
    host.hass.callWS = async request => {
      const state = window.importRecoveryState;
      if (request.section === "configuration" && request.action === "import_preview") {
        state.previews++;
        return {title:"Reviewed import", summary:{model:"gpt-5-mini", tools:0, function_groups:0, speech_rules:0}};
      }
      if (request.section === "configuration" && request.action === "import") {
        state.created.push({entry_id:"entry-1", subentry_id:`lost-import-${state.created.length + 1}`, title:"Created before disconnect"});
        throw {code:3, message:"Import reply lost"};
      }
      if (request.action === "agents" && !request.section && state.created.length) {
        state.reads++;
        if (state.failRead) throw {code:"offline", message:"Authoritative catalogue unavailable"};
        const catalogue = await send(request);
        return {...catalogue, agents:[...catalogue.agents, ...state.created]};
      }
      return send(request);
    };
  });
  await panel.locator(".agent-actions-menu > summary").click();
  await panel.locator("#import-agent").click();
  await panel.locator('input[name="import-mode"][value="new"]').check();
  await panel.locator("#import-document").fill("First reviewed source");
  await panel.locator("#import-preview").click();
  await expect(panel.locator("#import-apply")).toBeEnabled();
  await panel.locator("#import-apply").click();
  await expect(panel.locator("#import-apply")).toBeDisabled();
  await expect(panel.locator("#import-summary")).toContainText("may already have been applied");
  await panel.locator("#import-document").fill("Edited source after the lost reply");
  await panel.locator("#import-preview").click();
  await expect(panel.locator("#import-summary")).toContainText("Authoritative catalogue unavailable");
  await expect(panel.locator("#import-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#import-document")).toHaveValue("Edited source after the lost reply");
  await expect(panel.locator("#import-apply")).toBeDisabled();
  expect(await page.evaluate(() => ({created:importRecoveryState.created.length, previews:importRecoveryState.previews, reads:importRecoveryState.reads})))
    .toEqual({created:1, previews:1, reads:1});
  await page.evaluate(() => {importRecoveryState.failRead = false;});
  await panel.locator("#import-preview").click();
  await expect(panel.locator("#import-apply")).toBeEnabled();
  await expect(panel.locator("#import-document")).toHaveValue("Edited source after the lost reply");
  expect(await page.evaluate(() => ({created:importRecoveryState.created.length, previews:importRecoveryState.previews, reads:importRecoveryState.reads})))
    .toEqual({created:1, previews:2, reads:2});
  expect(await panel.evaluate(host => host._data.agents.some(agent => agent.subentry_id === "lost-import-1"))).toBe(true);
  await expectHarnessClean(page, errors);
});
