import {test, expect} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const actionType of ["local_action", "model_routing"]) for (const suffix of [0, 1, 2]) {
  test(`Rule response CRLF survives unrelated repeated saves: ${actionType} ${suffix} bundle=${bundled}`, async ({page}) => {
    const url = fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : "");
    await page.goto(url);
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-edit").first()).toBeVisible();
    const responses = {success_response:`left\nright\r\nlast ${suffix}`, failure_response:`failed\r\nlast ${suffix}`};
    await panel.evaluate(async (host, {responses, actionType}) => {
      const rule = structuredClone(host._result.rules[0]);
      rule.match_type = "contains";
      rule.action_type = actionType;
      rule.action = actionType === "local_action"
        ? {actions:[{action:"light.turn_off",target:{entity_id:"light.bedroom_lamp"}}],continue_to_ai:false,...responses}
        : {model:"gpt-4.1-mini",reasoning_effort:null,scope:"request",reset:false,continue_to_ai:true,success_response:responses.success_response};
      await host._call("request_rules","update",{rule_id:rule.id,rule,revision:host._result.revision});
    }, {responses, actionType});
    for (let save = 0; save < 2; save++) {
      await page.goto(url);
      await panel.locator(".rule-edit").first().click();
      await panel.locator("#rule-name").fill(`Preserved ${save}`);
      await panel.locator("#rule-save").click();
      await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open",true);
      const saved = await page.evaluate(() => browserHarness.getState().requestRules.rules[0].action);
      expect(saved.success_response).toBe(responses.success_response);
      if (actionType === "local_action") expect(saved.failure_response).toBe(responses.failure_response);
    }
    // An intentional visible edit remains authored text rather than being
    // replaced with the older CRLF snapshot on the next save.
    await panel.locator(".rule-edit").first().click();
    if (actionType === "model_routing") await panel.locator("#rule-continue-to-ai").uncheck();
    await panel.locator(actionType === "local_action" ? "#rule-success" : "#rule-routing-success").fill("New\nresponse");
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open",true);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].action.success_response)).toBe("New\nresponse");
  });
}

test("name-only rule edits preserve one multiline trigger and both responses", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".rule-edit").first()).toBeVisible();
  await panel.evaluate(async host => {
    const rule = structuredClone(host._result.rules[0]);
    Object.assign(rule, {name:"Multiline command", phrases:["turn off\nthe bedroom lamp"], match_type:"equals", action_type:"local_action",
      action:{actions:[{action:"light.turn_off",target:{entity_id:"light.bedroom_lamp"}}],continue_to_ai:false,
        success_response:"Bedroom lamp off.\nGood night.",failure_response:"Action failed.\nPlease try again."}});
    await host._call("request_rules","update",{rule_id:rule.id,rule,revision:host._result.revision});
  });
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await panel.locator(".rule-edit").first().click();
  await expect(panel.locator("#rule-phrases")).toHaveValue("turn off the bedroom lamp");
  await expect(panel.locator("#rule-success")).toHaveValue("Bedroom lamp off.\nGood night.");
  await expect(panel.locator("#rule-failure")).toHaveValue("Action failed.\nPlease try again.");
  await panel.locator("#rule-name").fill("Renamed multiline command");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open",true);
  const saved = await page.evaluate(()=>browserHarness.getState().requestRules.rules[0]);
  expect(saved.phrases).toEqual(["turn off\nthe bedroom lamp"]);
  expect(saved.action.success_response).toBe("Bedroom lamp off.\nGood night.");
  expect(saved.action.failure_response).toBe("Action failed.\nPlease try again.");
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await panel.locator(".rule-edit").first().click();
  await expect(panel.locator("#rule-success")).toHaveValue(saved.action.success_response);
});

for (const ending of ["\n", "\r\n"]) test(`reorder preserves verbose regex comments and multiline replacement through Save (${JSON.stringify(ending)})`, async ({page}) => {
  await page.goto(fixtureUrl("assistant/speech"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#add-regex")).toBeVisible();
  const rules = [{pattern:"(?x)\n\\bHA\\b  # Expand abbreviation\n| \\bHASS\\b",replacement:"Home\nAssistant"},{pattern:"XYZ",replacement:"Example"}];
  for (const rule of rules) for (const field of ["pattern","replacement"]) rule[field]=rule[field].replaceAll("\n",ending);
  await panel.evaluate(async (host,rules)=>{
    await host._call("configuration","update",{config:{...host._draft,speech_processing_enabled:true,speech_regex_replacements:rules},title:host._draftTitle,revision:host._configData.revision});
  },rules);
  await page.goto(fixtureUrl("assistant/speech"));
  await expect(panel.locator(".regex-pattern").first()).toHaveValue(rules[0].pattern.replace(/\r\n?/g,"\n"));
  await panel.locator('[data-regex-index="1"] .move-regex[data-direction="-1"]').click();
  await expect(panel.locator(".regex-pattern").nth(1)).toHaveValue(rules[0].pattern.replace(/\r\n?/g,"\n"));
  await expect(panel.locator(".regex-replacement").nth(1)).toHaveValue(rules[0].replacement.replace(/\r\n?/g,"\n"));
  await panel.locator("#save-config").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.speech_regex_replacements)).toEqual([rules[1],rules[0]]);
  await page.goto(fixtureUrl("assistant/speech"));
  await expect(panel.locator(".regex-pattern").nth(1)).toHaveValue(rules[0].pattern.replace(/\r\n?/g,"\n"));
  await expect(panel.locator(".regex-replacement").nth(1)).toHaveValue(rules[0].replacement.replace(/\r\n?/g,"\n"));
});

test("saving unrelated defaults preserves multiline wording alternatives", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator(".rule-edit").first()).toBeVisible();
  const groups=[{canonical:"turn\noff",alternatives:["switch\noff",'stop, "please"']}];
  await panel.evaluate(async (host,groups)=>{
    await host._call("request_rules","settings",{defaults:host._result.defaults,wording_groups:groups,revision:host._result.revision});
  },groups);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await panel.locator(".wording-editor summary").click();
  await expect(panel.locator(".wording-canonical")).toHaveValue(groups[0].canonical);
  await expect(panel.locator(".wording-alternatives")).toHaveValue('switch\noff, "stop, ""please"""');
  await panel.locator(".rule-settings summary").click();
  await panel.locator("#rules-default-word-forms").uncheck();
  await panel.locator("#save-page").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().requestRules.wording_groups)).toEqual(groups);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  await panel.locator(".wording-editor summary").click();
  await expect(panel.locator(".wording-canonical")).toHaveValue(groups[0].canonical);
});
