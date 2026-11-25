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

test("nightly capture and native response transitions remain stable across repeated edits", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");

  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Nightly capture input");
  await panel.locator("#rule-phrases").fill("ask {question}\nanswer {question}");
  await panel.locator("#rule-match").selectOption("sentence_pattern");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-continue-to-ai").check();
  await panel.locator("#rule-ai-input-mode").selectOption("capture");
  await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
  await panel.locator("#rule-save").click();

  for (let index = 0; index < 3; index += 1) {
    const card = panel.locator(".request-rule-card").filter({hasText:"Nightly capture input"});
    await card.locator(".rule-edit").click();
    await panel.locator("#rule-phrases").fill(`ask {question}\nanswer {${index % 2 ? "question" : "topic"}}`);
    if (index % 2 === 0) {
      await expect(panel.locator("#rule-ai-input-capture")).toHaveAttribute("aria-invalid", "true");
      await panel.locator("#rule-ai-input-mode").selectOption("original");
    } else {
      await panel.locator("#rule-ai-input-mode").selectOption("capture");
      await expect(panel.locator("#rule-ai-input-capture")).toHaveValue("question");
      await expect(panel.locator("#rule-ai-input-capture")).toHaveAttribute("aria-invalid", "false");
    }
    await panel.locator("#rule-save").click();
  }

  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Nightly Function response");
  await panel.locator("#rule-phrases").fill("nightly Function response");
  const sequence = index => [{
    action:"extended_openai_conversation_responses.call_function",
    data:{function:"baseline_tool", arguments:{}},
    response_variable:`reading_${index}`,
  }, {
    set_conversation_response:`Result {{ reading_${index}.result.value }}`,
  }];
  await setActions(panel, sequence(0));
  await panel.locator("#rule-success").fill("Function complete");
  await panel.locator("#rule-save").click();

  for (let index = 1; index <= 3; index += 1) {
    const card = panel.locator(".request-rule-card").filter({hasText:"Nightly Function response"});
    await card.locator(".rule-edit").click();
    await setActions(panel, sequence(index));
    await panel.locator("#rule-save").click();
    await card.locator(".rule-edit").click();
    const actions = await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => selector.value);
    expect(actions).toEqual(sequence(index));
    await expect(panel.locator("#rule-success")).toHaveValue("Function complete");
    await panel.locator(".rule-close").first().click();
  }

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator(".request-rule-card").filter({hasText:"Nightly Function response"}).locator(".rule-edit").click();
  expect(await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => selector.value)).toEqual(sequence(3));

  await testInfo.attach("request-rule-native-response-stress", {
    body:JSON.stringify({captureTransitions:3, responseUpdates:3, freshLoadVerified:true}, null, 2),
    contentType:"application/json",
  });
  console.log("ENHANCED REQUEST_RULE_NATIVE_RESPONSE capture_transitions=3 response_updates=3");
  await expectHarnessClean(page, errors);
});
