import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

for (const bundled of [false, true]) for (const inactiveMode of ["defaults", "sentence_pattern"]) {
  test(`Rule matching mode saves without retired fuzzy controls: ${inactiveMode} bundle=${bundled}`, async ({page}) => {
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator(".rule-edit").first().click();
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("gpt-4.1-mini");
    await panel.locator("#rule-match").selectOption("contains");
    await panel.locator("#rule-phrases").fill("route {room}");
    await panel.locator("#rule-advanced > summary").click();
    await panel.locator("#rule-matching-behavior").selectOption("custom");
    await expect(panel.locator("#rule-fuzzy")).toHaveCount(0);
    await expect(panel.locator("#rule-threshold")).toHaveCount(0);
    if (inactiveMode === "defaults") await panel.locator("#rule-matching-behavior").selectOption("defaults");
    else await panel.locator("#rule-match").selectOption("sentence_pattern");
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update"))).toHaveLength(1);
  });
}

for (const bundled of [false, true]) for (const returnToOriginal of [false, true]) {
  test(`Memory mode excludes inactive native date validation: bundle=${bundled} return=${returnToOriginal}`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, "persistent", 1, bundled);
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill("A valid authored fact");
    await panel.locator("#memory-type").selectOption("temporary");
    const expiry = panel.locator("#memory-expiry");
    await expiry.fill("2030-04-05T12:30");
    await expiry.press("ArrowLeft");
    await expiry.press("Backspace");
    expect(await expiry.evaluate(input => ({value:input.value, bad:input.validity.badInput}))).toEqual({value:"", bad:true});
    await panel.evaluate(host => {
      window.memorySubmits = 0;
      host.shadowRoot.querySelector("#memory-dialog form").addEventListener("submit", () => { window.memorySubmits++; });
    });
    await panel.locator("#memory-type").selectOption("persistent");
    await expect(expiry).toBeDisabled();
    expect(await expiry.evaluate(input => input.form.checkValidity())).toBe(true);
    expect(await expiry.evaluate(input => {
      input.disabled = false;
      const valid = input.form.checkValidity();
      input.disabled = true;
      return valid;
    })).toBe(false); // Calibration: the retained partial date still blocks an enabled field.
    if (returnToOriginal) {
      await panel.locator("#memory-type").selectOption("temporary");
      await expect(expiry).toBeEnabled();
      await expect(expiry).toHaveJSProperty("required", true);
      expect(await expiry.evaluate(input => input.validity.badInput)).toBe(true);
      await panel.locator("#memory-save").click();
      expect(await page.evaluate(() => memorySubmits)).toBe(0);
      expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => ["add","temporary_add"].includes(call.action)))).toEqual([]);
      await expiry.fill(await page.evaluate(() => new Date(Date.now() + 86400000).toISOString().slice(0,19)));
    }
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open", true);
    expect(await page.evaluate(() => memorySubmits)).toBe(1);
    const calls = await page.evaluate(() => dataCollectionBackend.calls.filter(call => ["add","temporary_add"].includes(call.action)));
    expect(calls).toHaveLength(1);
    expect(calls[0].action).toBe(returnToOriginal ? "temporary_add" : "add");
    if (!returnToOriginal) expect(calls[0]).not.toHaveProperty("expires_at");
    await expectHarnessClean(page, errors);
  });

  test(`Rule mode excludes inactive aliases, including recreated controls: bundle=${bundled} return=${returnToOriginal}`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator(".rule-edit").first().click();
    await panel.locator("#rule-match").selectOption("contains");
    await panel.locator("#rule-action-type").selectOption("local_action");
    const selector = panel.locator("#rule-action-sequence-host ha-selector");
    await selector.evaluate(control => {
      control.value = [{action:"extended_openai_conversation_responses.call_function",data:{function:"probe",arguments:{},result_alias:"valid_alias"}}];
      control.dispatchEvent(new CustomEvent("value-changed", {detail:{value:control.value},bubbles:true,composed:true}));
    });
    const alias = panel.locator(".rule-result-alias");
    await alias.fill("sensor result");
    await alias.press("Tab");
    expect(await alias.evaluate(input => input.validity.patternMismatch)).toBe(true);
    await panel.evaluate(host => {
      window.ruleSubmits = 0;
      host.shadowRoot.querySelector("#rule-form").addEventListener("submit", () => { window.ruleSubmits++; });
    });
    await panel.locator("#rule-save").click();
    expect(await page.evaluate(() => ruleSubmits)).toBe(0);
    await panel.locator("#rule-action-type").selectOption("model_routing");
    await panel.locator("#rule-model").fill("gpt-4.1-mini");
    await selector.evaluate(control => control.dispatchEvent(new CustomEvent("value-changed", {detail:{value:control.value},bubbles:true,composed:true})));
    await expect(alias).toBeDisabled();
    expect(await alias.evaluate(input => input.form.checkValidity())).toBe(true);
    expect(await alias.evaluate(input => {
      input.disabled = false;
      const valid = input.form.checkValidity();
      input.disabled = true;
      return valid;
    })).toBe(false);
    if (returnToOriginal) {
      await panel.locator("#rule-action-type").selectOption("local_action");
      await expect(alias).toBeEnabled();
      await expect(alias).toHaveValue("sensor result");
      await panel.locator("#rule-save").click();
      expect(await page.evaluate(() => ruleSubmits)).toBe(0);
      await alias.fill("corrected_alias");
      await alias.press("Tab");
    }
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).not.toHaveJSProperty("open", true);
    expect(await page.evaluate(() => ruleSubmits)).toBe(1);
    const calls = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update"));
    expect(calls).toHaveLength(1);
    expect(calls[0].rule.action_type).toBe(returnToOriginal ? "local_action" : "model_routing");
    if (returnToOriginal) expect(calls[0].rule.action.actions[0].data.result_alias).toBe("corrected_alias");
    else expect(calls[0].rule.action).not.toHaveProperty("actions");
    await expectHarnessClean(page, errors);
  });
}
