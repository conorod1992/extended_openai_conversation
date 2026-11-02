import {expect, test} from "@playwright/test";
import {readFileSync} from "node:fs";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test.beforeEach(async ({page}) => {
  await page.route("**/favicon.ico", route => route.fulfill({status:204, body:""}));
});
const reachabilityInventory = JSON.parse(readFileSync(new URL("../tests_stress/frontend_control_reachability_inventory.json", import.meta.url), "utf8"));

const pack = {
  format:"extended_openai_request_rule_pack", version:1, groups:[],
  rules:[{
    id:"retained-source", name:"Retained sharing rule", enabled:true,
    phrases:["retained sharing phrase"], match_type:"equals", action_type:"model_routing",
    action:{model:"gpt-5-mini", reasoning_effort:"", scope:"request", reset:false, continue_to_ai:true, success_response:"Updated"},
    matching_behavior:"defaults", matching:{word_forms:true, wording_alternatives:true, fuzzy:false, fuzzy_threshold:90},
    order:0, conditions:[], group_id:null, continue_matching:false, ai_input_mode:"original", ai_input_capture:null,
  }],
};

async function openSharing(panel) {
  const disclosure = panel.locator("#rule-sharing");
  if (!await disclosure.evaluate(element => element.open)) await disclosure.locator("summary").click();
  return disclosure;
}

async function choosePack(panel) {
  await panel.locator("#rule-pack-file").setInputFiles({name:"retained-pack.json", mimeType:"application/json", buffer:Buffer.from(JSON.stringify(pack))});
  await panel.locator("#rule-pack-review-button").click();
}

test("Rule Sharing preserves its reviewed file and export choices across disclosure and binding lifetimes", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  const sharing = await openSharing(panel);
  await choosePack(panel);
  await expect(panel.locator("#rule-pack-confirm")).toBeVisible();
  await panel.locator("#rule-pack-selection").selectOption("selected");
  await panel.locator("#rule-pack-reviewed-custom").check();
  await panel.locator("#rule-pack-file").evaluate(element => { window.retainedSharingFile = element; });
  for (let cycle = 0; cycle < 3; cycle++) {
    await sharing.locator("summary").click();
    // Rebinding a retained route must replace its own listeners, not create
    // another lazy initializer or clear the already reviewed source.
    await panel.evaluate(async host => {
      const module = await import("/custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js");
      module.bindRequestRules(host);
    });
    await sharing.locator("summary").click();
    await expect(panel.locator("#rule-pack-confirm")).toBeVisible();
    await expect(panel.locator("#rule-pack-selection")).toHaveValue("selected");
    await expect(panel.locator("#rule-pack-reviewed-custom")).toBeChecked();
    expect(await panel.locator("#rule-pack-file").evaluate(element => ({same:element === window.retainedSharingFile, name:element.files[0]?.name})))
      .toEqual({same:true, name:"retained-pack.json"});
  }
  await panel.locator("#rule-pack-confirm").click();
  await expect(panel.locator("#rule-pack-message")).toContainText("1 disabled rules imported");
  await expect(panel.locator(".request-rule-card").filter({hasText:"Retained sharing rule"}).locator(".rule-enabled")).not.toBeChecked();
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.action === "rule_pack_import").length)).toBe(1);
  await expectHarnessClean(page, errors);
});

test("failed Sharing initialization can retry and a failed review retains the source and other authored choices", async ({page}) => {
  const errors = trackPageErrors(page);
  for (const viewport of reachabilityInventory.viewports) {
    await page.setViewportSize({width:viewport.width, height:viewport.height});
    await page.goto(fixtureUrl("capabilities/request-rules"));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator("#rule-sharing-content").evaluate(content => {
      Object.defineProperty(content, "innerHTML", {configurable:true, set() {
        delete this.innerHTML;
        throw new Error("Sharing initialization witness");
      }});
    });
    const sharing = await openSharing(panel);
    await expect(sharing.getByRole("alert")).toContainText("Sharing initialization witness");
    const retry = sharing.locator("[data-retry-rule-sharing]");
    await retry.scrollIntoViewIfNeeded();
    await retry.click({trial:true});
    await retry.click();
    await expect(panel.locator("#rule-pack-file")).toBeVisible();
    await panel.locator("#rule-pack-selection").selectOption("group");
    await panel.locator("#rule-pack-reviewed-custom").check();
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      let failed = false;
      browserHarness.hass.callWS = request => {
        if (!failed && request.action === "rule_pack_review") {
          failed = true;
          return Promise.reject({code:"invalid_request", message:"Review refresh witness"});
        }
        return original(request);
      };
    });
    await choosePack(panel);
    await expect(panel.locator("#rule-pack-message")).toContainText("Review refresh witness");
    await sharing.locator("summary").click();
    await sharing.locator("summary").click();
    await expect(panel.locator("#rule-pack-selection")).toHaveValue("group");
    await expect(panel.locator("#rule-pack-reviewed-custom")).toBeChecked();
    expect(await panel.locator("#rule-pack-file").evaluate(element => element.files[0]?.name)).toBe("retained-pack.json");
    await panel.locator("#rule-pack-review-button").click();
    await expect(panel.locator("#rule-pack-confirm")).toBeVisible();
  }
  await expectHarnessClean(page, errors);
});

test("repeated Request Rules binding keeps one wording-add and one create-rule activation", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".wording-editor > summary").click();
  await expect(panel.locator("#wording-add")).toBeVisible();
  const before = await panel.locator(".wording-group").count();
  await panel.evaluate(async host => {
    const {bindRequestRulesCore} = await import("/custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js");
    window.ruleCreateActivations = 0;
    for (let pass = 0; pass < 4; pass++) bindRequestRulesCore(host, {openEditor:() => { window.ruleCreateActivations++; }});
  });
  await panel.locator("#wording-add").click();
  await expect(panel.locator(".wording-group")).toHaveCount(before + 1);
  await panel.locator("#rule-add").click();
  expect(await page.evaluate(() => window.ruleCreateActivations)).toBe(1);
  // An open disclosure can be restored/replaced before its lazy feature binds.
  // Its earlier toggle event must not be the only initialization opportunity.
  await panel.evaluate(async host => {
    const previous = host.shadowRoot.querySelector("#rule-sharing");
    const mounted = previous.cloneNode(true);
    const toggled = new Promise(resolve => mounted.addEventListener("toggle", resolve, {once:true}));
    mounted.open = true;
    await toggled;
    previous.replaceWith(mounted);
    const {bindRequestRulesCore} = await import("/custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js");
    bindRequestRulesCore(host);
  });
  await expect(panel.locator("#rule-pack-file")).toBeVisible();
  await expectHarnessClean(page, errors);
});
