import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function setActions(panel, value) {
  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  await selector.evaluate((node, next) => {
    node.value = next;
    node.dispatchEvent(new CustomEvent("value-changed", {
      detail: {value: next},
      bubbles: true,
      composed: true,
    }));
  }, value);
}

test("captured AI input stays valid only while every sentence pattern provides the selected slot", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");

  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Captured question");
  await panel.locator("#rule-phrases").fill("deep think {question}\ncarefully answer {question}");
  await panel.locator("#rule-match").selectOption("sentence_pattern");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-continue-to-ai").check();
  await panel.locator("#rule-ai-input-mode").selectOption("capture");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveAttribute("aria-invalid", "false");
  await panel.locator("#rule-save").click();

  let card = panel.locator(".request-rule-card").filter({hasText:"Captured question"});
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-ai-input-mode")).toHaveValue("capture");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");

  await panel.locator("#rule-phrases").fill("deep think {question}\ncarefully answer {topic}");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveAttribute("aria-invalid", "true");
  await expect(panel.locator("#rule-ai-input-help")).toContainText("No captured values are available for every trigger");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#rule-error")).toContainText("No captured values are available for every trigger");

  await panel.locator("#rule-ai-input-mode").selectOption("original");
  await expect(panel.locator("#rule-ai-capture-label")).toBeHidden();
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".request-rule-card").filter({hasText:"Captured question"});
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-ai-input-mode")).toHaveValue("original");
  await expect(panel.locator("#rule-phrases")).toHaveValue("deep think {question}\ncarefully answer {topic}");

  await panel.locator("#rule-phrases").fill("deep think {question}\ncarefully answer {question}");
  await panel.locator("#rule-ai-input-mode").selectOption("capture");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveAttribute("aria-invalid", "false");
  await panel.locator("#rule-save").click();

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator(".request-rule-card").filter({hasText:"Captured question"}).locator(".rule-edit").click();
  await expect(panel.locator("#rule-ai-input-mode")).toHaveValue("capture");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
  await expectHarnessClean(page, errors);
});

for (const bundle of [false, true]) {
  test(`native Function responses and nested actions round-trip (${bundle ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
    await panel.locator("#rule-name").fill("Battery response");
    await panel.locator("#rule-phrases").fill("battery status");
    const actions = [{
      action:"extended_openai_conversation_responses.call_function",
      data:{function:"shadowed_tool", arguments:{message:"{{ request.text }}"}},
      data_template:{function:"baseline_tool"},
      response_variable:"battery",
    }, {
      if:[{condition:"template", value_template:"{{ battery.result.level is number }}"}],
      then:[{set_conversation_response:"Battery {{ battery.result.level }} percent"}],
    }];
    await setActions(panel, actions);
    await expect(panel.locator(".rule-result-alias")).toHaveCount(0);
    await panel.locator("#rule-success").fill("Battery check complete");
    await panel.locator("#rule-save").click();

    await page.goto(fixtureUrl("capabilities/request-rules", bundle ? "&bundle=1" : ""));
    const saved = panel.locator(".request-rule-card").filter({hasText:"Battery response"});
    await saved.locator(".rule-edit").click();
    const selector = panel.locator("#rule-action-sequence-host ha-selector");
    expect(await selector.evaluate(node => node.value)).toEqual(actions);
    await expect(panel.locator("#rule-success")).toHaveValue("Battery check complete");
    await expectHarnessClean(page, errors);
  });
}

test("legacy Function responses require explicit review and preserve authored actions until edited", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  const card = panel.locator(".request-rule-card").first();
  await expect(card).toBeVisible();
  const legacy = [
    {
      action:"extended_openai_conversation_responses.call_function",
      data:{function:"baseline_tool", result_alias:"reading", step_id:"legacy-step"},
      response_variable:"battery",
    },
    {set_conversation_response:"Battery {{ battery.result.result.level }} percent"},
  ];
  await panel.evaluate((host, actions) => {
    const rule = host._result.rules[0];
    Object.assign(rule, {
      action_type:"local_action",
      action:{actions, success_response:"Battery {reading.level}", failure_response:"Battery unavailable"},
    });
    host._result.diagnostics[rule.id] = "Function response format changed. Review all native Function response variables.";
  }, legacy);
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-action-repair")).toContainText("Function response format changed.");
  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  expect(await selector.evaluate(node => node.value)).toEqual(legacy);
  await expect(panel.locator("#rule-success")).toHaveValue("Battery {reading.level}");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Confirm that you reviewed");
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update"))).toEqual([]);

  const repaired = [
    {...legacy[0], data:{function:"baseline_tool"}},
    {set_conversation_response:"Battery {{ battery.result.level }} percent"},
  ];
  await setActions(panel, repaired);
  await panel.locator("#rule-success").fill("Battery check complete");
  await panel.locator("#rule-native-response-reviewed").check();
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
  const requests = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update"));
  expect(requests).toHaveLength(1);
  expect(requests[0].confirm).toBe(true);
  expect(requests[0].rule.action.actions).toEqual(repaired);
  expect(requests[0].rule.action.success_response).toBe("Battery check complete");
  await expectHarnessClean(page, errors);
});
