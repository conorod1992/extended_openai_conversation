import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const backendUrl = process.env.REAL_HA_BACKEND_URL;
const failCatalogue = process.env.BULK_REPAIR_FAIL_CATALOGUE === "1";
test.skip(!backendUrl, "requires the genuine Home Assistant management bridge");
const fixture = "/tests_browser/real-ha-fixture.html?bundle=1&route=capabilities%2Ffunctions&backend=" + encodeURIComponent(backendUrl);

test("bulk repair acknowledges genuine HA state without depending on post-save reads", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = page.locator("extended-openai-management-panel");
  const tool = {
    spec:{name:"bulk_repaired_tool", description:"Repaired through the browser", parameters:{type:"object", properties:{}}},
    function:{type:"native", name:"get_user_from_user_id"},
  };
  await page.goto(fixture);
  await expect(panel.locator("#function-repair-editor")).toBeVisible();
  const initial = await panel.evaluate(host => ({revision:host._configData.revision, title:host._configData.title}));

  // Only post-commit reads fail. The mutation and its acknowledgement pass
  // through the real authenticated HA WebSocket bridge unchanged.
  await page.evaluate(failCatalogue => {
    const {panel, hass} = window.browserHarness;
    panel._draft = {...panel._draft, prompt:"Unsaved prompt survives repair"};
    panel._draftTitle = "Unsaved title survives repair";
    const original = hass.callWS.bind(hass);
    window.bulkRepairProbe = {mutations:0, catalogueFailures:0, original};
    hass.callWS = async message => {
      const probe = window.bulkRepairProbe;
      if (probe.saved && message.section === "configuration" && message.action === "get") {
        throw new Error("Post-save configuration read unavailable");
      }
      if (probe.saved && message.action === "agents" && failCatalogue) {
        probe.catalogueFailures++;
        throw new Error("Post-save catalogue unavailable");
      }
      const result = await original(message);
      if (message.section === "function_repair" && message.action === "save") {
        probe.mutations++;
        probe.saved = structuredClone(result);
        await new Promise(resolve => {probe.releaseFirstAcknowledgement = resolve;});
      }
      return result;
    };
  }, failCatalogue);
  await panel.locator("#function-repair-editor").fill(JSON.stringify([tool], null, 2));
  await panel.locator("#function-repair-save").click();
  await page.waitForFunction(() => window.bulkRepairProbe.releaseFirstAcknowledgement);
  const later = "- spec: [newer raw edits made before the first acknowledgement";
  await panel.locator("#function-repair-editor").fill(later);
  await page.evaluate(() => window.bulkRepairProbe.releaseFirstAcknowledgement());
  await expect(panel.locator("#toast")).toContainText("Function Tools repaired");
  await expect(panel.locator("#toast")).not.toContainText("Unable to repair");
  if (failCatalogue) await expect(panel.locator("#toast")).toContainText("could not be refreshed");

  const acknowledged = await page.evaluate(() => {
    const host = window.browserHarness.panel, probe = window.bulkRepairProbe;
    return {
      saved:probe.saved, mutations:probe.mutations, catalogueFailures:probe.catalogueFailures,
      baseline:host._configData, draft:host._draft, draftTitle:host._draftTitle,
    };
  });
  expect(acknowledged.mutations).toBe(1);
  expect(acknowledged.saved.config.functions).toEqual([tool]);
  expect(acknowledged.saved.revision).not.toBe(initial.revision);
  expect(acknowledged.baseline.revision).toBe(acknowledged.saved.revision);
  expect(acknowledged.baseline.config.functions).toEqual([tool]);
  expect(acknowledged.baseline.config.prompt).toBe("Saved prompt before bulk repair");
  expect(acknowledged.baseline.title).toBe(initial.title);
  expect(acknowledged.draft.functions).toEqual([tool]);
  expect(acknowledged.draft.prompt).toBe("Unsaved prompt survives repair");
  expect(acknowledged.draftTitle).toBe("Unsaved title survives repair");
  expect(acknowledged.catalogueFailures).toBe(failCatalogue ? 1 : 0);

  // The first write cleared quarantine. Its still-authored fallback editor must
  // remain usable after optional read failures and submit a second real write.
  await page.evaluate(async () => {
    const {panel,hass} = window.browserHarness,probe = window.bulkRepairProbe;
    hass.callWS = async message => {
      const result = await probe.original(message);
      if(message.section === "function_repair" && message.action === "save") {
        probe.mutations++;
        probe.saved = structuredClone(result);
      }
      return result;
    };
    await panel._loadAgents();
  });
  await expect(panel.locator("#function-repair-editor")).toHaveValue(later);
  await expect(panel.locator("#function-repair-save")).toBeEnabled();
  const followup = {...tool,spec:{...tool.spec,description:"A later edit saved after quarantine cleared"}};
  await panel.locator("#function-repair-editor").fill(JSON.stringify([followup],null,2));
  await panel.locator("#function-repair-save").click();
  await expect.poll(() => page.evaluate(() => window.bulkRepairProbe.mutations)).toBe(2);
  await expect(panel.locator("#function-repair-editor")).toHaveCount(0);
  const second = await page.evaluate(() => window.bulkRepairProbe.saved);
  expect(second.config.functions).toEqual([followup]);
  expect(second.revision).not.toBe(acknowledged.saved.revision);
  expect(await panel.evaluate(host => host._draft.prompt)).toBe("Unsaved prompt survives repair");
  expect(await panel.evaluate(host => host._draftTitle)).toBe("Unsaved title survives repair");

  // A fresh document verifies persisted HA state independently of the response
  // and discards the fault injection without replaying the repair.
  await page.goto(fixture);
  await expect(panel.getByRole("heading", {name:tool.spec.name, exact:true})).toBeVisible();
  await expect(panel.locator("#function-repair-editor")).toHaveCount(0);
  const fresh = await panel.evaluate(host => host._call("configuration", "get"));
  expect(fresh.config.functions).toEqual([followup]);
  expect(fresh.config.prompt).toBe("Saved prompt before bulk repair");
  expect(fresh.revision).toBe(second.revision);
  expect(fresh.function_repair).toBeUndefined();
  expect(await page.evaluate(() => window.browserHarness.calls.filter(call => call.section === "function_repair" && call.action === "save").length)).toBe(2);
  await expectHarnessClean(page, errors);
});
