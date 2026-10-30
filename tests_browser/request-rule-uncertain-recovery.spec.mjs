import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

const cases = [
  {outcome:"complete", transition:"after"},
  {outcome:"unknown", transition:"after"},
  {outcome:"unknown", transition:"pending"},
  {outcome:"unknown", transition:"authentication"},
  {outcome:"complete", transition:"authentication"},
];
for (const bundled of [false, true]) for (const {outcome, transition} of cases) test(`Request Rule recovered create retains outcome ownership: ${outcome}/${transition} (${bundled ? "bundle" : "source"})`, async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  const acknowledged = outcome === "complete" && transition === "after";
  await expect(panel.getByRole("button", {name:"Create rule", exact:true}).first()).toBeVisible();
  await panel.evaluate((host, outcome) => {
    host.hass = {...host.hass, user:{...host.hass.user, id:"rule-recovery-review-owner"}};
    const send = host.hass.callWS.bind(host.hass);
    window.ruleRecoveryWrites = [];
    window.ruleRecoveryReads = 0;
    host.hass.callWS = async request => {
      if (request.section === "request_rules" && request.action === "list") ruleRecoveryReads++;
      if (request.section !== "request_rules" || !["create", "update"].includes(request.action)) return send(request);
      ruleRecoveryWrites.push(structuredClone(request));
      const result = await send(request);
      if (ruleRecoveryWrites.length === 1) {
        await new Promise(resolve => {window.releaseRuleRecoveryReply = resolve;});
        if (outcome === "unknown") throw {code:"unknown_error", message:"Unknown error"};
      }
      return result;
    };
  }, outcome);
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Committed once");
  await panel.locator("#rule-phrases").fill("review recovery command");
  await panel.locator("#rule-action-sequence-host ha-selector").evaluate(selector => {
    const value = [{event:"review_rule_recovery"}];
    selector.value = value;
    selector.dispatchEvent(new CustomEvent("value-changed", {detail:{value},bubbles:true,composed:true}));
  });
  await panel.locator("#rule-save").click();
  await expect.poll(() => page.evaluate(() => typeof releaseRuleRecoveryReply)).toBe("function");
  if (!acknowledged) await panel.locator("#rule-name").fill("Retained next edit");
  if (transition === "after") {
    await page.evaluate(() => releaseRuleRecoveryReply());
    await expect(panel.locator("#rule-save")).toBeEnabled();
    if (acknowledged) await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
    else await expect(panel.locator("#rule-error")).toContainText("Unknown error");
  }
  if (transition === "authentication") await panel.evaluate(host => {
    const original = host.hass;
    host.hass = {...original, user:{...original.user, id:"intervening-rule-owner"}};
    host.hass = original;
  });
  await panel.evaluate(host => {
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next); next.hass = hass;
  });
  if (transition !== "after") await page.evaluate(() => releaseRuleRecoveryReply());
  const id = await page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.name === "Committed once").id);
  if (acknowledged) {
    await panel.locator(`[data-rule-key="${id}"] .rule-edit`).click();
    await panel.locator("#rule-name").fill("Retained next edit");
  }
  await expect(panel.locator("#rule-name")).toHaveValue("Retained next edit");
  const reads = await page.evaluate(() => ruleRecoveryReads);
  await panel.locator("#rule-save").click();
  if (!acknowledged) {
    await expect.poll(() => page.evaluate(reads => ruleRecoveryReads > reads || ruleRecoveryWrites.length > 1, reads)).toBe(true);
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
  }
  await expect(panel.locator("#rule-save")).toBeEnabled();
  const stored = await page.evaluate(() => browserHarness.getState().requestRules.rules);
  const writes = await page.evaluate(() => ruleRecoveryWrites);
  expect(stored).toHaveLength(2);
  expect(writes.filter(request => request.action === "create")).toHaveLength(1);
  if (acknowledged) {
    expect(writes.map(request => request.action)).toEqual(["create", "update"]);
    expect(stored.find(rule => rule.id === id).name).toBe("Retained next edit");
  } else {
    expect(writes).toHaveLength(1);
    // Presentation cannot authorize the same create, even if a control is reset.
    await panel.evaluate(host => {
      const button = host.shadowRoot.querySelector("#rule-save");
      button.disabled = false; button.click();
    });
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await page.evaluate(() => ruleRecoveryWrites.length)).toBe(1);
    await panel.locator(".rule-close").last().click();
    await panel.locator(`[data-rule-key="${id}"] .rule-edit`).click();
    await panel.locator("#rule-name").fill("Reviewed after recovery");
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
    expect(await page.evaluate(() => ruleRecoveryWrites.map(request => request.action))).toEqual(["create", "update"]);
    expect(await page.evaluate(id => browserHarness.getState().requestRules.rules.find(rule => rule.id === id).name, id)).toBe("Reviewed after recovery");
  }
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.length)).toBe(2);
});
