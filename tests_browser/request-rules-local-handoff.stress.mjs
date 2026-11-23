import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function setActions(panel, actions) {
  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  await selector.evaluate((node, value) => {
    node.value = value;
    node.dispatchEvent(new CustomEvent("value-changed", {
      detail:{value},
      bubbles:true,
      composed:true,
    }));
  }, actions);
}

test("nightly local handoffs survive repeated authoring and fresh load", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");

  const created = [];
  for (let index = 0; index < 5; index += 1) {
    const name = `Nightly local handoff ${index + 1}`;
    created.push(name);
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill(name);
    await panel.locator("#rule-phrases").fill("nightly local handoff");
    await panel.locator("#rule-match").selectOption("equals");
    await setActions(panel, [{
      action:"light.turn_on",
      target:{entity_id:`light.handoff_${index + 1}`},
    }]);
    if (index < 4) await panel.locator("#rule-local-continue-to-ai").check();
    if (index === 2) {
      await panel.locator("#rule-add-conditions").click();
      await panel.locator("#rule-condition-host ha-selector").evaluate((node) => {
        const value=[{condition:"state",entity_id:"input_boolean.nightly_gate",state:"on"}];
        node.value=value;
        node.dispatchEvent(new CustomEvent("value-changed",{detail:{value},bubbles:true,composed:true}));
      });
    }
    await panel.locator("#rule-save").click();
  }

  for (let index = 0; index < created.length; index += 1) {
    const card = panel.locator(".request-rule-card").filter({hasText:created[index]});
    await card.locator(".rule-edit").click();
    await expect(panel.locator("#rule-local-continue-to-ai")).toBeChecked({checked:index < 4});
    if (index === 2) await expect(panel.locator("#rule-conditions-body")).toBeVisible();
    await panel.locator(".rule-close").first().click();
  }

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  const state = await page.evaluate(() => browserHarness.getState().requestRules.rules
    .filter((rule) => rule.name.startsWith("Nightly local handoff "))
    .map((rule) => ({
      name:rule.name,
      order:rule.order,
      handoff: Boolean(rule.action.continue_to_ai),
      condition_count:(rule.conditions || []).length,
    })));
  expect(state.map((item) => item.name)).toEqual(created);
  expect(state.map((item) => item.handoff)).toEqual([true,true,true,true,false]);
  expect(state.map((item) => item.condition_count)).toEqual([0,0,1,0,0]);

  await testInfo.attach("request-rule-local-handoffs", {
    body:JSON.stringify({rules:state.length, handoffs:4, conditionalRules:1, freshLoadVerified:true}, null, 2),
    contentType:"application/json",
  });
  console.log("ENHANCED REQUEST_RULE_HANDOFF rules=5 handoffs=4");
  await expectHarnessClean(page, errors);
});
