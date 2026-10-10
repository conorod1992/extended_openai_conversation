import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("duplicating a maximum-length Function opens an editable unique name", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".duplicate-tool").first()).toBeAttached();
  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    const call = panel._call.bind(panel);
    panel._call = (section, action, payload) => section === "tools" && action === "serialize"
      ? Promise.resolve({yaml:JSON.stringify(payload.tool)}) : call(section, action, payload);
    const tool = panel._draft.functions[0];
    tool.spec.name = "a".repeat(64);
    panel._draft.functions.push({...structuredClone(tool), spec:{...structuredClone(tool.spec), name:`${"a".repeat(59)}_copy`}});
    panel._render();
  });
  const duplicate = panel.locator(".duplicate-tool").first();
  await duplicate.evaluate(button => {
    for (let parent = button.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  });
  await duplicate.click();
  await expect(panel.locator("#tool-yaml")).toBeEditable();
  await expect(panel.locator("#tool-yaml")).toHaveValue(new RegExp(`${"a".repeat(57)}_copy_2`));
  await expectHarnessClean(page, errors);
});

test("switching assistants resets rule filtering and refreshes provider details", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toBeVisible();
  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    panel._data.agents.push({...panel._data.agents[0], subentry_id:"agent-2", title:"Azure assistant", provider:"Azure", model:"ha-production"});
    panel._ruleGroupFilter = "previous-agent-only";
    panel._render();
  });
  await panel.locator("#agent").selectOption("agent-2");
  await expect(panel.locator("#rule-group-filter")).toHaveValue("all");
  await expect(panel.locator(".agent-picker small")).toHaveText("Azure · ha-production");
  await page.evaluate(() => {
    const panel = window.browserHarness.panel;
    panel._selectedAgent().model = "new-deployment";
    panel._render();
  });
  await expect(panel.locator(".agent-picker small")).toHaveText("Azure · new-deployment");
  await expectHarnessClean(page, errors);
});

test("retention change and revert clears unsaved state through delegated listeners", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/retention", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  const control = panel.locator('[data-retention-config="usage_request_retention_days"]');
  await expect(control).toBeVisible();
  const original = await control.inputValue();
  const options = await control.locator("option").evaluateAll(items => items.map(item => item.value));
  await control.selectOption(options.find(value => value !== original));
  await expect(panel.getByText("Unsaved changes", {exact:true})).toBeVisible();
  await control.selectOption(original);
  await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);
  await expectHarnessClean(page, errors);
});
