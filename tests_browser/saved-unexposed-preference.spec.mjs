import {expect,test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

test("removed saved-unexposed preference disappears from pending displayed state", async ({page}) => {
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel=page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="exposed_entities_enabled"]').waitFor();
  await page.evaluate(()=>{
    const key="extended-openai-browser-harness-state-v3";
    const state=JSON.parse(localStorage.getItem(key));
    state.configuration.exposed_attribute_catalog={entities:[], saved_unexposed:[{
      entity_id:"light.audit_hidden",reference:"registry:audit-hidden",name:"Hidden audit lamp",
      selected_attributes:["brightness"],registry_entry_exists:true,
    }]};
    state.configuration.config.exposed_entity_attributes={"registry:audit-hidden":["brightness"]};
    localStorage.setItem(key,JSON.stringify(state));
  });
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const card=panel.locator('[data-saved-exposed-reference="registry:audit-hidden"]');
  await expect(card).toContainText("brightness");
  await card.getByRole("button",{name:"Remove saved preference"}).click();
  expect(await panel.evaluate(h=>h._draft.exposed_entity_attributes)).toEqual({});
  expect(await panel.evaluate(h=>h._configDirty)).toBe(true);
  // The backend still contains the saved baseline, so this asserts the draft view.
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.exposed_entity_attributes)).toEqual({"registry:audit-hidden":["brightness"]});
  await expect(card).toHaveCount(0);
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(card).toHaveCount(0);
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let failed = false;
    hass.callWS = request => {
      if (!failed && request.section === "configuration" && request.action === "save") {
        failed = true;
        return Promise.reject(new Error("Regression save failure"));
      }
      return original(request);
    };
  });
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configBusy)).toBeFalsy();
  expect(await panel.evaluate(host => host._configDirty)).toBe(true);
  await expect(card).toHaveCount(0);
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.exposed_entity_attributes)).toEqual({"registry:audit-hidden":["brightness"]});
  await panel.locator("#save-config").click();
  await expect.poll(() => panel.evaluate(host => host._configDirty)).toBe(false);
  expect(await page.evaluate(() => browserHarness.getState().configuration.config.exposed_entity_attributes)).toEqual({});
  await page.goto(fixtureUrl("assistant/prompt-context"));
  await panel.locator('[data-config="exposed_entities_enabled"]').waitFor();
  await expect(card).toHaveCount(0);
  expect(await panel.evaluate(host => host._draft.exposed_entity_attributes)).toEqual({});
});