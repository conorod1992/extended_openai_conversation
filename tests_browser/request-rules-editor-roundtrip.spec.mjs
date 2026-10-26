import {expect, test} from "@playwright/test";
import {acceptSharingReview, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const panelFor = (page) => page.locator("extended-openai-management-panel");

async function createGroup(panel, name) {
  await panel.locator("#rule-groups-manage").click();
  await expect(panel.locator("#rule-groups-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-new-group-name").fill(name);
  await panel.locator("#rule-group-add").click();
  const id = await panel.evaluate((host, label) => host._result.groups.find((group) => group.name === label)?.id || null, name);
  expect(id).toBeTruthy();
  await panel.locator("#rule-groups-done").click();
  return id;
}

async function setHaSelector(locator, value) {
  await locator.evaluate((selector, next) => {
    selector.value = next;
    selector.dispatchEvent(new CustomEvent("value-changed", {
      detail: {value: next},
      bubbles: true,
      composed: true,
    }));
  }, value);
}

async function openRule(panel, name) {
  const card = panel.locator(".request-rule-card").filter({hasText:name});
  await expect(card).toBeVisible();
  await card.locator(".rule-edit").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  return card;
}

async function assertLocalRuleEditor(panel, {groupId, conditions, actions}) {
  await expect(panel.locator("#rule-name")).toHaveValue("Round-trip local");
  await expect(panel.locator("#rule-phrases")).toHaveValue("local round trip\nlocal alternate");
  await expect(panel.locator("#rule-match")).toHaveValue("contains");
  await expect(panel.locator("#rule-action-type")).toHaveValue("local_action");
  await expect(panel.locator("#rule-group")).toHaveValue(groupId);
  await expect(panel.locator("#rule-local-continue-to-ai")).not.toBeChecked();
  await expect(panel.locator("#rule-success")).toHaveValue("Local success");
  await expect(panel.locator("#rule-failure")).toHaveValue("Local failure");
  await expect(panel.locator("#rule-ai-input-mode")).toHaveValue("original");
  await expect(panel.locator("#rule-continue-matching")).toBeChecked();
  await expect(panel.locator("#rule-matching-behavior")).toHaveValue("custom");
  await expect(panel.locator("#rule-word-forms")).not.toBeChecked();
  await expect(panel.locator("#rule-wording")).not.toBeChecked();
  await expect(panel.locator("#rule-fuzzy")).toBeChecked();
  await expect(panel.locator("#rule-threshold")).toHaveValue("83");
  await expect(panel.locator("#rule-conditions-body")).toBeVisible();
  expect(await panel.locator("#rule-condition-host ha-selector").evaluate((selector) => selector.value)).toEqual(conditions);
  expect(await panel.locator("#rule-action-sequence-host ha-selector").evaluate((selector) => selector.value)).toEqual(actions);
}

async function assertRoutingRuleEditor(panel, {groupId}) {
  await expect(panel.locator("#rule-name")).toHaveValue("Round-trip routing");
  await expect(panel.locator("#rule-phrases")).toHaveValue("route round trip");
  await expect(panel.locator("#rule-match")).toHaveValue("starts_with");
  await expect(panel.locator("#rule-action-type")).toHaveValue("model_routing");
  await expect(panel.locator("#rule-group")).toHaveValue(groupId);
  await expect(panel.locator("#rule-model")).toHaveValue("gpt-5-mini");
  await expect(panel.locator("#rule-reasoning")).toHaveValue("medium");
  await expect(panel.locator("#rule-scope")).toHaveValue("request");
  await expect(panel.locator("#rule-reset")).not.toBeChecked();
  await expect(panel.locator("#rule-continue-to-ai")).toBeChecked();
  await expect(panel.locator("#rule-ai-input-mode")).toHaveValue("original");
  await expect(panel.locator("#rule-continue-matching")).not.toBeChecked();
  await expect(panel.locator("#rule-matching-behavior")).toHaveValue("defaults");
}

test("Request Rule editor round-trips local and routing forms through reopen and fresh load", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  let panel = panelFor(page);
  const groupId = await createGroup(panel, "Round-trip group");

  const actions = [{
    action: "light.turn_on",
    target: {entity_id: "light.round_trip"},
    data: {brightness_pct: 42},
  }];
  const conditions = [{
    condition: "and",
    conditions: [
      {condition: "state", entity_id: "input_boolean.round_trip_ready", state: "on"},
      {condition: "numeric_state", entity_id: "sensor.round_trip_level", above: 10},
    ],
  }];

  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Round-trip local");
  await panel.locator("#rule-phrases").fill("local round trip\nlocal alternate");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-group").selectOption(groupId);
  await setHaSelector(panel.locator("#rule-action-sequence-host ha-selector"), actions);
  await panel.locator("#rule-success").fill("Local success");
  await panel.locator("#rule-failure").fill("Local failure");
  await panel.locator("#rule-add-conditions").click();
  await setHaSelector(panel.locator("#rule-condition-host ha-selector"), conditions);
  await panel.locator("#rule-advanced summary").click();
  await panel.locator("#rule-continue-matching").check();
  await panel.locator("#rule-matching-behavior").selectOption("custom");
  await panel.locator("#rule-word-forms").uncheck();
  await panel.locator("#rule-wording").uncheck();
  await panel.locator("#rule-fuzzy").check();
  await panel.locator("#rule-threshold").fill("83");
  await panel.locator("#rule-save").click();

  await openRule(panel, "Round-trip local");
  await assertLocalRuleEditor(panel, {groupId, conditions, actions});
  await panel.locator(".rule-close").first().click();

  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Round-trip routing");
  await panel.locator("#rule-phrases").fill("route round trip");
  await panel.locator("#rule-match").selectOption("starts_with");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-group").selectOption(groupId);
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await expect.poll(() => panel.locator("#rule-reasoning option").allTextContents()).toContain("Medium");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("request");
  await panel.locator("#rule-continue-to-ai").check();
  await panel.locator("#rule-save").click();

  await openRule(panel, "Round-trip routing");
  await assertRoutingRuleEditor(panel, {groupId});

  // Reset is a distinct routing mode. This browser fixture verifies the editor
  // fields that the UI owns; backend normalization is covered by Python tests.
  await panel.locator("#rule-reset").check();
  await panel.locator("#rule-continue-to-ai").uncheck();
  await expect(panel.locator("#rule-scope")).toBeDisabled();
  await expect(panel.locator("#rule-scope")).toHaveValue("conversation");
  await panel.locator("#rule-routing-success").fill("Defaults restored");
  await panel.locator("#rule-save").click();

  await openRule(panel, "Round-trip routing");
  await expect(panel.locator("#rule-reset")).toBeChecked();
  await expect(panel.locator("#rule-continue-to-ai")).not.toBeChecked();
  await expect(panel.locator("#rule-scope")).toHaveValue("conversation");
  await expect(panel.locator("#rule-routing-success")).toHaveValue("Defaults restored");
  await panel.locator(".rule-close").first().click();

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = panelFor(page);
  await openRule(panel, "Round-trip local");
  await assertLocalRuleEditor(panel, {groupId, conditions, actions});
  await panel.locator(".rule-close").first().click();

  await openRule(panel, "Round-trip routing");
  await expect(panel.locator("#rule-reset")).toBeChecked();
  await expect(panel.locator("#rule-continue-to-ai")).not.toBeChecked();
  await expect(panel.locator("#rule-scope")).toHaveValue("conversation");
  await expect(panel.locator("#rule-routing-success")).toHaveValue("Defaults restored");
  await panel.locator(".rule-close").first().click();

  await expectHarnessClean(page, errors);
});


for (const bundled of [false, true]) {
  test(`mounted Rule editor shares routing scope and reasoning controls (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = panelFor(page);
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      browserHarness.hass.callWS = async message => {
        const result = await original(message);
        if (message.type.endsWith("/model_catalog")) return {...result, reasoning_effort_options: message.model === "gpt-4o" ? [] : ["low", "high"]};
        return result;
      };
    });
    await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await panel.locator("#rule-name").fill("Shared routing controls");
    await panel.locator("#rule-phrases").fill("route command");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-advanced summary").click();
    for (const match of ["equals", "starts_with", "ends_with", "contains", "sentence_pattern"]) {
      await panel.locator("#rule-match").selectOption(match);
      for (const ai of [false, true]) for (const matching of [false, true]) {
        await panel.locator("#rule-continue-to-ai").check();
        await panel.locator("#rule-scope").selectOption("request");
        await panel.locator("#rule-continue-matching").setChecked(matching);
        await panel.locator("#rule-continue-to-ai").setChecked(ai);
        await expect(panel.locator('#rule-scope option[value="request"]')).toHaveJSProperty("disabled", !ai && !matching);
        await expect(panel.locator("#rule-scope")).toHaveValue(!ai && !matching ? "conversation" : "request");
      }
    }
    await panel.locator("#rule-model").fill("gpt-5.6");
    await expect(panel.locator('#rule-reasoning option[value="high"]')).toHaveCount(1);
    await panel.locator("#rule-reasoning").selectOption("high");
    await panel.locator("#rule-model").fill("gpt-4o");
    await expect(panel.locator("#rule-reasoning")).toHaveValue("");
    await expect(panel.locator("#rule-reasoning option")).toHaveCount(1);
    await panel.locator("#rule-match").selectOption("contains");
    await panel.locator("#rule-continue-matching").uncheck();
    await panel.locator("#rule-continue-to-ai").uncheck();
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
    const saved = await page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.name === "Shared routing controls"));
    expect(saved.action.scope).toBe("conversation");
    expect(saved.action.reasoning_effort || "").toBe("");
    await expectHarnessClean(page, errors);
  });
}

function sharingGroupPack() {
  return {format:"extended_openai_request_rule_pack",version:1,
    groups:[{id:"portable-group",name:"Imported group"}],
    rules:[{id:"portable-rule",name:"Imported grouped rule",enabled:false,phrases:["shared group"],match_type:"equals",action_type:"model_routing",action:{model:"gpt-5-mini",scope:"conversation",continue_to_ai:false,success_response:"Updated"},matching_behavior:"defaults",matching:{},order:0,conditions:[],group_id:"portable-group",continue_matching:false,ai_input_mode:"original",ai_input_capture:null}]};
}

async function openSharingWithReview(page, bundled, seedGroup = false) {
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = panelFor(page);
  if (seedGroup) await createGroup(panel, "Existing selection");
  await panel.locator("#rule-sharing > summary").click();
  await panel.locator("#rule-pack-file").setInputFiles({name:"group-pack.json",mimeType:"application/json",buffer:Buffer.from(JSON.stringify(sharingGroupPack()))});
  await panel.locator("#rule-pack-review-button").click();
  await expect(panel.locator(".rule-pack-review")).toBeVisible();
  await panel.locator("#rule-pack-selection").selectOption("group");
  await panel.evaluate(host => {
    host.__sharingPreserved = [host.shadowRoot.querySelector("#rule-pack-file"),host.shadowRoot.querySelector("#rule-pack-review .rule-pack-review"),host.shadowRoot.querySelector("#rule-pack-group")];
  });
  return panel;
}

async function expectSharingPreserved(panel) {
  expect(await panel.evaluate(host => {
    const [file,review,select] = host.__sharingPreserved;
    return file === host.shadowRoot.querySelector("#rule-pack-file") && file.files[0]?.name === "group-pack.json" && review === host.shadowRoot.querySelector("#rule-pack-review .rule-pack-review") && select === host.shadowRoot.querySelector("#rule-pack-group") && host.shadowRoot.querySelector("#rule-sharing").open;
  })).toBe(true);
}

for (const bundled of [false, true]) {
  test(`Sharing group choices follow create rename and delete without losing review (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openSharingWithReview(page, bundled);
    const id = await createGroup(panel, "Kitchen");
    const select = panel.locator("#rule-pack-group");
    await expect(select.locator(`option[value="${id}"]`)).toHaveText("Kitchen");
    await select.selectOption(id);
    await expectSharingPreserved(panel);
    await panel.locator("#rule-groups-manage").click();
    const row = panel.locator(`.rule-group-row[data-group-id="${id}"]`);
    await row.locator(".rule-group-name").fill("Kitchen & <Dining>");
    await row.locator(".rule-group-rename").click();
    await expect(select.locator(`option[value="${id}"]`)).toHaveText("Kitchen & <Dining>");
    await expect(select).toHaveValue(id);
    await panel.locator("#rule-groups-done").click();
    await expectSharingPreserved(panel);
    await panel.locator("#rule-groups-manage").click();
    await row.locator(".rule-group-delete").click();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#confirm-accept").click();
    await expect(select.locator(`option[value="${id}"]`)).toHaveCount(0);
    await expect(select).toHaveValue("");
    await expect(select.locator('option[value=""]')).toHaveText("Ungrouped");
    await panel.locator("#rule-groups-done").click();
    await expectSharingPreserved(panel);
    await expectHarnessClean(page, errors);
  });

  test(`Sharing group choices include an imported group immediately (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openSharingWithReview(page, bundled, true);
    const existingId = await panel.evaluate(host => host._result.groups.find(group => group.name === "Existing selection").id);
    await panel.locator("#rule-pack-group").selectOption(existingId);
    await panel.locator("#rule-pack-confirm").click();
    await expect(panel.locator("#rule-pack-message")).toContainText("1 disabled rules imported");
    const groupId = await panel.evaluate(host => host._result.groups.find(group => group.name === "Imported group")?.id);
    expect(groupId).toBeTruthy();
    await expect(panel.locator("#rule-pack-group")).toHaveValue(existingId);
    await expect(panel.locator(`#rule-pack-group option[value="${groupId}"]`)).toHaveText("Imported group");
    await panel.locator("#rule-pack-group").selectOption(groupId);
    await expect(panel.locator("#rule-sharing")).toHaveJSProperty("open", true);
    expect(await panel.locator("#rule-pack-file").evaluate(input => input.files[0].name)).toBe("group-pack.json");
    const download = page.waitForEvent("download");
    await panel.locator("#rule-pack-reviewed-custom").check();
    await panel.locator("#rule-pack-export").click();
    await acceptSharingReview(panel);
    await download;
    const request = await page.evaluate(() => browserHarness.calls.filter(call => call.action === "rule_pack_export").at(-1));
    expect(request.group_id).toBe(groupId);
    expect(request.selection).toBe("group");
    await expectHarnessClean(page, errors);
  });
}


for (const bundled of [false, true]) {
  test(`Azure routing edits preserve reasoning through deployment changes (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = panelFor(page);
    await panel.evaluate(host => {
      host._result = {...host._result, api_provider: "azure", effective_model: "gpt-5.6"};
      const original = host._hass.callWS.bind(host._hass);
      host._hass.callWS = async message => {
        const result = await original(message);
        if (message.type.endsWith("/model_catalog")) {
          host._azureRuleLookups ||= [];
          host._azureRuleLookups.push(message.model);
          return {...result, reasoning_effort_options: message.model === "gpt-5.6" ? ["low", "high"] : []};
        }
        return result;
      };
    });
    await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await panel.locator("#rule-name").fill("Azure preserved route");
    await panel.locator("#rule-phrases").fill("switch deployment");
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("home-deployment");
    await expect(panel.locator('#rule-reasoning option[value="high"]')).toHaveCount(1);
    await panel.locator("#rule-reasoning").selectOption("high");
    await panel.locator("#rule-save").click();
    await openRule(panel, "Azure preserved route");
    await expect(panel.locator("#rule-reasoning")).toHaveValue("high");
    // An unrelated input refresh used to look up the deployment and clear High.
    await panel.locator("#rule-phrases").fill("switch to another deployment");
    await panel.locator("#rule-model").fill("another-deployment");
    await expect.poll(() => panel.evaluate(host => host._azureRuleLookups.length)).toBeGreaterThan(2);
    await expect(panel.locator("#rule-reasoning")).toHaveValue("high");
    expect(await panel.evaluate(host => host._azureRuleLookups.every(model => model === "gpt-5.6"))).toBe(true);
    await panel.locator("#rule-save").click();
    const saved = await page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.name === "Azure preserved route"));
    expect(saved.action.model).toBe("another-deployment");
    expect(saved.action.reasoning_effort).toBe("high");
    await expectHarnessClean(page, errors);
  });

  test(`mixed-case Function actions retain editable result aliases (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = panelFor(page);
    await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await setHaSelector(panel.locator("#rule-action-sequence-host ha-selector"), [{
      action: "EXTENDED_OPENAI_CONVERSATION_RESPONSES.CALL_FUNCTION",
      data_template: {function: "EchoCase", arguments: {}, result_alias: "reply"},
    }]);
    await expect(panel.locator(".rule-result-alias")).toHaveValue("reply");
    await expect(panel.locator("#rule-result-aliases")).toContainText("EchoCase");
    await panel.locator(".rule-result-alias").fill("renamed_reply");
    await panel.locator(".rule-result-alias").dispatchEvent("change");
    const actions = await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => selector.value);
    expect(actions[0].data_template.result_alias).toBe("renamed_reply");
    expect(actions[0].action).toBe("EXTENDED_OPENAI_CONVERSATION_RESPONSES.CALL_FUNCTION");
    expect(actions[0].data_template.function).toBe("EchoCase");
    await expectHarnessClean(page, errors);
  });
}


for (const bundled of [false, true]) {
  test(`Azure rule editing preserves unbound deployment reasoning (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(`/tests_browser/fixture.html?route=capabilities/request-rules${bundled ? "&bundle=1" : ""}`);
    const panel = panelFor(page);
    await expect(panel.locator("#rule-add")).toBeVisible();
    await panel.evaluate(host => {
      const call = host._hass.callWS.bind(host._hass);
      host._azureLookups = [];
      host._hass = {...host._hass, callWS:async message => {
        if (message.type?.endsWith("/model_catalog")) {
          host._azureLookups.push(message.model);
          return {reasoning_effort_options:message.model === "gpt-5.6" ? ["low", "medium", "high"] : []};
        }
        if (message.section === "request_rules" && message.action === "update") host._savedAzureRule = message.rule;
        return call(message);
      }};
      host._result = {...host._result, api_provider:"azure", azure_model:null, effective_model:"gpt-4.1",
        rules:[{id:"unbound-azure", name:"Unbound Azure", enabled:true, phrases:["route"], match_type:"equals", action_type:"model_routing",
          action:{model:"gpt-5.6", reasoning_effort:"high", scope:"conversation", continue_to_ai:true}, matching_behavior:"defaults", matching:{}, order:0, conditions:[]}]};
      host._render();
    });
    await openRule(panel, "Unbound Azure");
    await expect.poll(() => panel.evaluate(host => host._azureLookups.at(-1))).toBe("gpt-5.6");
    await expect(panel.locator("#rule-reasoning")).toHaveValue("high");
    await panel.locator("#rule-save").click();
    await expect.poll(() => panel.evaluate(host => host._savedAzureRule?.action?.reasoning_effort)).toBe("high");
    await expectHarnessClean(page, errors);
  });
}
