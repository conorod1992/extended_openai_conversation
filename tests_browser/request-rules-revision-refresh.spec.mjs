import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function createRule(panel, name) {
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill(name);
  await panel.locator("#rule-phrases").fill(name.toLowerCase());
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-save").click();
  await expect(panel.locator(".request-rule-card").filter({hasText:name})).toBeVisible();
}

async function enforceBackendRevision(page) {
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    window.revisionBackendCall = original;
    browserHarness.hass.callWS = async message => {
      if (message.section === "request_rules" && ["create", "update", "duplicate"].includes(message.action)
          && message.revision !== browserHarness.getState().requestRules.revision) {
        throw new Error("Request Rules changed in another tab. Reload the latest rules before saving.");
      }
      return original(message);
    };
  });
}

test("switching agents and saving configuration refreshes the rules list before the next create", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toBeVisible();
  await enforceBackendRevision(page);
  await panel.locator("#agent").selectOption("scale-agent-1");
  await panel.evaluate(async host => {
    await host._call("request_rules", "duplicate", {rule_id:host._result.rules[0].id});
  });
  await panel.locator("#agent").selectOption("agent-1");
  await panel.locator("#agent").selectOption("scale-agent-1");

  const readsBefore = await page.evaluate(() => browserHarness.calls.filter(
    call => call.section === "request_rules" && call.action === "list",
  ).length);
  await panel.evaluate(async host => {
    await host._navigate("assistant", "prompt-context");
    host._draft.prompt = `${host._draft.prompt || ""} Unrelated prompt edit`;
    host._syncConfigDirty();
    const {saveConfiguration} = await import("/custom_components/extended_openai_conversation_responses/frontend/management-actions.js");
    await saveConfiguration(host, host.shadowRoot.querySelector("#save-config"));
    await host._navigate("capabilities", "request-rules");
  });
  expect(await page.evaluate(() => browserHarness.calls.filter(
    call => call.section === "request_rules" && call.action === "list",
  ).length)).toBeGreaterThan(readsBefore);
  await createRule(panel, "After unrelated prompt edit");
  await expectHarnessClean(page, errors);
});

test("an authoritative reload replaces a stale tracked revision while preserving concurrent-edit rejection", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".request-rule-card")).toBeVisible();
  await enforceBackendRevision(page);
  await panel.evaluate(async host => {
    await host._call("request_rules", "duplicate", {rule_id:host._result.rules[0].id});
    // An independent writer uses the transport directly, bypassing this tab's
    // mutation acknowledgement and cached list.
    await window.revisionBackendCall({
      type:"extended_openai_conversation_responses/management", section:"request_rules", action:"duplicate",
      entry_id:"entry-1", subentry_id:"agent-1", rule_id:host._result.rules[0].id,
      revision:browserHarness.getState().requestRules.revision,
    });
    let rejected = false;
    try {
      await host._call("request_rules", "create", {rule:{name:"Stale create"}});
    } catch (err) {
      rejected = err.message.includes("another tab");
    }
    if (!rejected) throw new Error("A concurrent edit was not rejected");
    await host._loadSection();
  });
  // Row controls resolve implicit revisions from the tracked acknowledgement;
  // the rule editor separately guards its authoritative list snapshot.
  const toggle = panel.locator(".request-rule-card").first().locator(".rule-enabled");
  await toggle.uncheck();
  await expect(toggle).not.toBeChecked();
  await createRule(panel, "After authoritative reload");
  await expectHarnessClean(page, errors);
});
