import fs from "node:fs";
import {expect, test} from "@playwright/test";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";

const source = process.env.EOAI_GENERATED_CASES;
test.skip(!source, "requires the generated native HA runtime");

test("generated multilingual programmes retain semantics through native editor round trips", async ({context, page}) => {
  test.setTimeout(180000);
  const cases = JSON.parse(fs.readFileSync(source, "utf8"));
  const phase = process.env.EOAI_GENERATED_PHASE;
  const panel = await openColdHaRoute(context, page, "capabilities/functions");
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const item of cases) {
    const name = item.tool.spec.name;
    const card = panel.locator(`[data-tool-key="${name}"]`);
    if (phase === "author") {
      await panel.locator("#function-add").click();
      await panel.locator("#add-tool").click();
      await replaceNativeYaml(page, panel.locator("#tool-yaml-native"), item.yaml);
      await panel.locator("#tool-validate").click();
      await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
      await panel.locator("#tool-save").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
      await expect(card).toBeVisible();
      // Reopen the committed native document, edit only its description and save.
      await card.locator(".edit-tool").click();
      const editor = panel.locator("#tool-yaml-native");
      await expect.poll(() => editor.evaluate(element => element.yaml)).toContain(name);
      const serialized = await editor.evaluate(element => element.yaml);
      const edited = serialized.replace(/^  description:.*$/m, "  description: " + JSON.stringify("Reviewed harmless edit " + item.caption));
      expect(edited).not.toBe(serialized);
      await replaceNativeYaml(page, editor, edited);
      await panel.locator("#tool-save").click();
      await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    }
    const persisted = await panel.evaluate(host => host._call("configuration", "get"));
    const expected = structuredClone(item.tool);
    expected.spec.description = "Reviewed harmless edit " + item.caption;
    const saved = persisted.config.functions.find(tool => tool.spec.name === name);
    expect(saved.spec).toEqual(expected.spec);
    expect(saved.function).toEqual(expected.function);
  }

  await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
  await expect(panel.locator("#rule-add")).toBeVisible();
  if (phase === "author") {
    await panel.evaluate(host => {
      const original = host.hass.callWS.bind(host.hass);
      window.__compositionGroupWrites = 0;
      host.hass.callWS = message => {
        if (message.section === "request_rules" && message.action === "groups") window.__compositionGroupWrites++;
        return original(message);
      };
    });
    await panel.locator("#rule-groups-manage").click();
    const group = panel.locator("#rule-new-group-name");
    await group.dispatchEvent("compositionstart", {data: ""});
    await group.fill("组合 " + cases[0].caption);
    for (const composing of [{isComposing: true, keyCode: 13}, {isComposing: false, keyCode: 229}]) {
      await group.dispatchEvent("keydown", {key: "Enter", code: "Enter", ...composing});
      expect(await page.evaluate(() => window.__compositionGroupWrites)).toBe(0);
    }
    await group.dispatchEvent("compositionend", {data: "组合 " + cases[0].caption});
    await group.press("Enter");
    await expect(panel.locator(".rule-group-name")).toHaveValue("组合 " + cases[0].caption);
    expect(await page.evaluate(() => window.__compositionGroupWrites)).toBe(1);
    await panel.locator("#rule-groups-done").click();
  }
  for (const item of cases) {
    const editedName = "Reviewed " + item.rule_name;
    if (phase === "author") {
      await panel.locator("#rule-add").click();
      const input = panel.locator("#rule-name");
      await input.dispatchEvent("compositionstart", {data: ""});
      await input.fill(item.rule_name);
      await input.dispatchEvent("keydown", {key: "Enter", code: "Enter", isComposing: true, keyCode: 229});
      await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
      await input.dispatchEvent("compositionend", {data: item.rule_name});
      await panel.locator("#rule-phrases").fill(item.rule_phrase);
      await panel.locator("#rule-match").selectOption("equals");
      await panel.locator("#rule-action-type").selectOption("model_routing");
      await panel.locator("#rule-model").fill("gpt-5.6");
      await panel.locator("#rule-continue-to-ai").uncheck();
      await panel.locator("#rule-routing-success").fill("Routed " + item.caption);
      await panel.locator("#rule-add-conditions").click();
      // Native HA selector input, followed by a real YAML edit and visual return.
      const selector = panel.locator("#rule-condition-host > ha-selector");
      await selector.evaluate(element => {
        const value = [{condition: "or", conditions: [{condition: "template", value_template: "{{ true }}"}, {condition: "template", value_template: "{{ false }}"}]}];
        element.value = value;
        element.dispatchEvent(new CustomEvent("value-changed", {detail: {value}, bubbles: true, composed: true}));
      });
      await panel.locator("#rule-save").click();
      await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
      const card = panel.locator(".request-rule-card").filter({hasText: item.rule_name});
      await card.locator(".rule-edit").click();
      await panel.locator("#rule-name").fill(editedName);
      const row = panel.locator("#rule-condition-host ha-automation-condition-row").first();
      await row.evaluate(element => { element._yamlMode = true; element.requestUpdate(); });
      await row.locator("ha-expansion-panel").evaluate(element => { element.expanded = true; });
      const editor = row.locator("ha-yaml-editor");
      await replaceNativeYaml(page, editor, "condition: or\nconditions:\n  - condition: template\n    value_template: '{{ true }}'\n  - condition: template\n    value_template: '{{ false }}'\n");
      await row.evaluate(element => { element._yamlMode = false; element.requestUpdate(); });
      await expect(row.locator("ha-yaml-editor")).toHaveCount(0);
      await panel.locator("#rule-save").click();
      await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    }
    const result = await panel.evaluate(host => host._call("request_rules", "list"));
    const rule = result.rules.find(rule => rule.name === editedName);
    expect(rule.phrases).toEqual([item.rule_phrase]);
    expect(rule.conditions).toEqual([{condition: "or", conditions: [{condition: "template", value_template: "{{ true }}"}, {condition: "template", value_template: "{{ false }}"}]}]);
    expect(rule.action.success_response).toBe("Routed " + item.caption);
    expect(rule.action.continue_to_ai).toBe(false);
  }
  expect(errors).toEqual([]);
});
