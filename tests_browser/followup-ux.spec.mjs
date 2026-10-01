import {expect,test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

test("cold attribute editor selects, switches, closes, and preserves saved preferences", async ({page}) => {
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="exposed_entities_enabled"]').waitFor();
  await page.evaluate(()=>{
    const key="extended-openai-browser-harness-state-v3";
    const state=JSON.parse(localStorage.getItem(key));
    state.configuration.exposed_attribute_catalog={entities:[{entity_id:"light.kitchen",reference:"registry:kitchen",name:"Kitchen",attributes:["brightness"],selected_attributes:[],durable_selection_available:true},{entity_id:"light.hall",reference:"registry:hall",name:"Hall",attributes:["brightness"],selected_attributes:[],durable_selection_available:true}],saved_unexposed:[]};
    state.configuration.config.exposed_entities_enabled=true;
    localStorage.setItem(key,JSON.stringify(state));
  });
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const picker=panel.locator("#exposed-entity-picker-fallback");
  await picker.selectOption("light.kitchen");
  await expect(panel.locator('[data-exposed-editor]')).toContainText("light.kitchen");
  await picker.selectOption("light.hall");
  await expect(panel.locator('[data-exposed-editor]')).toContainText("light.hall");
  await panel.locator('[data-exposed-attribute=""][data-attribute="brightness"]').check();
  await panel.locator('[data-close-exposed-editor]').click();
  await expect(panel.locator('[data-exposed-editor]')).toHaveCount(0);
  await panel.locator('[data-edit-exposed-entity="light.hall"]').click();
  await expect(panel.locator('[data-exposed-editor]')).toContainText("light.hall");
  await expect(panel.locator('[data-attribute="brightness"]')).toBeChecked();
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.goto(fixtureUrl("assistant/prompt-context"));
  await panel.locator('[data-edit-exposed-entity="light.hall"]').click();
  await expect(panel.locator('[data-attribute="brightness"]')).toBeChecked();
});

test("local command search explains no matches and clears without losing selection", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/home-assistant"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="local_intents_enabled"]').waitFor();
  await panel.evaluate(h=>{h._result.local_handling={supported:true,intents:[{intent:"HassTurnOn",label:"Turn on devices"}]};h._draft.local_intents_enabled=true;h._render();});
  const selected=panel.locator('[data-local-intent-exclusion]');
  await selected.check();
  await panel.locator('#local-intent-search').fill('d');
  await expect(panel.locator('[data-local-intent-choice]').first()).toBeVisible();
  await panel.locator('#local-intent-search').fill('de');
  await expect(panel.locator('[data-local-intent-choice]').first()).toBeVisible();
  await expect(panel.locator('#local-intents-empty')).toBeHidden();
  const delayedChoice=panel.locator('[data-local-intent-choice]').first();
  const delayedCheckbox=delayedChoice.locator('input[type="checkbox"]');
  await expect(delayedCheckbox).toHaveCSS('width','18px');
  await expect(delayedCheckbox).toHaveCSS('height','18px');
  const [titleBox,descriptionBox]=await Promise.all([
    delayedChoice.locator('strong').boundingBox(),
    delayedChoice.locator('small').boundingBox(),
  ]);
  expect(descriptionBox.y).toBeGreaterThan(titleBox.y);
  await panel.locator('#local-intent-search').fill('nonexistentcommandxyz');
  await expect(panel.locator('#local-intents-empty')).toBeVisible();
  await expect(panel.locator('[data-local-intent-choice]:visible')).toHaveCount(0);
  await panel.locator('#local-intents-clear').click();
  await expect(panel.locator('#local-intents-empty')).toBeHidden();
  await expect(selected).toBeChecked();
  await expect(selected).toBeVisible();
});

test("inactive Local Handling summarizes saved exclusions and restores the editable list", async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.goto(fixtureUrl("capabilities/home-assistant"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="local_intents_enabled"]').waitFor();
  await panel.evaluate(host=>{
    host._draft.local_intents_enabled=false;
    host._draft.local_intent_exclusions=["HassTurnOn","HassSetTimer"];
    host._draft.local_intent_delayed_commands_to_ai=true;
    host._result.local_handling={supported:true,intents:[
      {intent:"HassTurnOn",label:"Turn on devices"},
      {intent:"HassSetTimer",label:"Set a timer"},
      {intent:"HassGetState",label:"Check device state"},
    ]};
    host._render();
  });

  const choices=panel.locator(".local-handling-dependent");
  await expect(choices.locator(".local-handling-saved-summary")).toContainText("3 command types are set to continue to AI");
  await expect(choices.locator(".local-handling-review")).not.toHaveAttribute("open", "");
  await expect(choices.locator("[data-local-intent-choice]:visible")).toHaveCount(0);
  await expect(choices.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toBeChecked();
  await expect(choices.locator('[data-local-intent-exclusion][value="HassSetTimer"]')).toBeChecked();
  await expect(choices.locator('[data-config="local_intent_delayed_commands_to_ai"]')).toBeChecked();
  await expect.poll(()=>panel.evaluate(host=>host._draft.local_intent_exclusions)).toEqual(["HassTurnOn","HassSetTimer"]);

  await choices.locator(".local-handling-review>summary").click();
  await expect(choices.locator("[data-local-intent-choice]:visible")).toHaveCount(4);
  await expect(choices.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toBeDisabled();
  await choices.locator(".local-handling-review>summary").click();
  await panel.locator('[data-config="local_intents_enabled"]').check();
  await expect(choices.locator(".local-handling-saved-summary")).toBeHidden();
  await expect(choices.locator("[data-local-intent-choice]:visible")).toHaveCount(4);
  await expect(choices.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toBeEnabled();
  await expect(choices.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toBeChecked();
  await expect.poll(()=>panel.evaluate(host=>host._draft.local_intent_exclusions)).toEqual(["HassTurnOn","HassSetTimer"]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test("disabled Model and Speech explanations remain readable while their controls stay disabled", async ({page}) => {
  await page.goto(fixtureUrl("assistant/model-responses"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator("#reset-model-parameters").waitFor();
  const temperatureState=await panel.evaluate(host=>{
    const capabilities={...host._result.model_capabilities,supports_temperature:false};
    host._result.model_capabilities=capabilities;
    host._configData.model_capabilities=capabilities;
    host._eocMainMarkup=null;
    host._render();
    const control=host.shadowRoot.querySelector("#config-temperature");
    if(!control)return null;
    const field=control.closest(".setting"),help=field.querySelector("small");
    document.documentElement.style.setProperty("--secondary-text-color","rgb(196, 196, 196)");
    return {disabled:control.disabled,controlOpacity:getComputedStyle(control).opacity,fieldOpacity:getComputedStyle(field).opacity,help:help.textContent,helpOpacity:getComputedStyle(help).opacity,helpColor:getComputedStyle(help).color};
  });
  expect(temperatureState).not.toBeNull();
  expect(temperatureState.disabled).toBe(true);
  expect(temperatureState.controlOpacity).toBe("0.55");
  expect(temperatureState.fieldOpacity).toBe("1");
  expect(temperatureState.help).toContain("Higher values make responses more varied");
  expect(temperatureState.helpOpacity).toBe("1");
  expect(temperatureState.helpColor).toBe("rgb(196, 196, 196)");

  await page.goto(fixtureUrl("assistant/speech"));
  await panel.locator('[data-config="speech_processing_enabled"]').waitFor();
  const speechState=await panel.evaluate(host=>{
    document.documentElement.style.setProperty("--secondary-text-color","rgb(196, 196, 196)");
    const control=host.shadowRoot.querySelector('[data-config="speech_strip_markdown"]');
    const section=control.closest('[data-dependent="speech_processing_enabled"]'),help=section.querySelector(".subheading>p"),button=section.querySelector("#add-regex");
    return {disabled:control.disabled,sectionOpacity:getComputedStyle(section).opacity,buttonOpacity:getComputedStyle(button).opacity,help:help.textContent,helpOpacity:getComputedStyle(help).opacity,helpColor:getComputedStyle(help).color};
  });
  expect(speechState.disabled).toBe(true);
  expect(speechState.sectionOpacity).toBe("1");
  expect(speechState.buttonOpacity).toBe("0.55");
  expect(speechState.help).toContain("Streaming speech is disabled while custom replacements are active");
  expect(speechState.helpOpacity).toBe("1");
  expect(speechState.helpColor).toBe("rgb(196, 196, 196)");
});

test("group validation follows corrected fields and preserves unrelated server errors", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('#add-group').click();
  await panel.locator('#group-description').fill('Test description');
  await panel.locator('#group-save').click();
  await expect(panel.locator('#group-error')).toHaveText('Group name is required.');
  await panel.locator('#group-name').fill('Corrected name');
  await expect(panel.locator('#group-error')).toHaveText('');
  await panel.locator('#group-id').fill('INVALID');
  await panel.locator('#group-save').click();
  await panel.locator('#group-name').fill('Another name');
  await expect(panel.locator('#group-error')).toContainText('Group ID must');
  await panel.locator('#group-id').fill('valid');
  await expect(panel.locator('#group-error')).toHaveText('');
  await panel.locator('#group-description').fill('');
  await panel.locator('#group-save').click();
  await panel.locator('#group-description').fill('Corrected description');
  await expect(panel.locator('#group-error')).toHaveText('');
  await panel.evaluate(h=>{const e=h.shadowRoot.querySelector('#group-error');delete e.dataset.field;e.textContent='Conflict: reload required';});
  await panel.locator('#group-name').fill('Final name');
  await expect(panel.locator('#group-error')).toHaveText('Conflict: reload required');
});

test("Request Rule card reports the final conversation response rather than its success fallback", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel=page.locator('extended-openai-management-panel');
  await panel.locator('#rule-add').waitFor();
  await panel.evaluate(host=>{
    host._result.rules=[{id:"final-response",name:"Final response regression",enabled:true,phrases:["test response"],match_type:"equals",action_type:"local_action",action:{success_response:"Done",failure_response:"Failed",actions:[{set_conversation_response:"Actually finished"}]}}];
    host._render();
  });
  const card=panel.locator('.request-rule-card').filter({hasText:'Final response regression'});
  await expect(card).toContainText('replies “Actually finished”');
  await expect(card).not.toContainText('replies “Done”');
});
