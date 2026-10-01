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
  await panel.locator('#local-intent-search').fill('nonexistentcommandxyz');
  await expect(panel.locator('#local-intents-empty')).toBeVisible();
  await expect(panel.locator('[data-local-intent-choice]:visible')).toHaveCount(0);
  await panel.locator('#local-intents-clear').click();
  await expect(panel.locator('#local-intents-empty')).toBeHidden();
  await expect(selected).toBeChecked();
  await expect(selected).toBeVisible();
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
