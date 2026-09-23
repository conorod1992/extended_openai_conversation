import {expect, test} from "@playwright/test";
import {fixtureUrl, acceptConfirmation, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const unload = (page) => page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable: true})));
const panelFor = (page) => page.locator("extended-openai-management-panel");
async function rejectOnce(page, section, action) {
  await page.evaluate(({section, action}) => {
    const hass = window.browserHarness.hass, original = hass.callWS.bind(hass);
    let rejected = false;
    hass.callWS = async (request) => {
      if (!rejected && request.section === section && request.action === action) { rejected = true; throw Error("Save rejected for regression test"); }
      return original(request);
    };
  }, {section, action});
}

for (const [view, field, section, action, removed] of [
  ["capabilities/guest-mode", "#guest-controls-enabled", "guest_mode", "save_policy", "#guest-policy-save"],
  ["capabilities/quiet-hours", "#qh-enabled", "quiet_hours", "update", "#qh-save,#qh-reset"],
]) {
  test(`${view}: shared bar, exact dirty state, discard, protected navigation and failed save`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(view)); const panel = panelFor(page);
    const control = panel.locator(field);
    // Guest controls intentionally live under Advanced.
    await control.evaluate((input) => { for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true; });
    await expect(panel.locator(removed)).toHaveCount(0);
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await control.check(); await expect(panel.locator("#save-page")).toHaveText("Save changes");
    expect(await unload(page)).toBe(true);
    await expect(panel.locator('.top-nav button[data-page="capabilities"]')).toHaveClass(/eoc-has-unsaved/);
    await control.uncheck(); await expect(panel.locator(".save-bar")).toHaveCount(0); expect(await unload(page)).toBe(false);
    await control.check();
    await panel.locator("#local-section").selectOption("request-rules", {force: true});
    await panel.locator("#confirm-cancel").click();
    await expect(panel.locator("#local-section")).toHaveValue(view.split("/")[1]);
    await panel.locator('.top-nav button[data-page="overview"]').click();
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
    await panel.locator("#confirm-cancel").click(); await expect(control).toBeChecked();
    await panel.locator("#discard-page").click(); await expect(control).not.toBeChecked(); expect(await unload(page)).toBe(false);
    await control.evaluate((input) => { for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true; });
    await control.check(); await rejectOnce(page, section, action);
    await panel.locator("#save-page").click();
    await expect(panel.locator("#toast")).toContainText("Save rejected");
    await expect(control).toBeChecked(); await expect(panel.locator("#save-page")).toBeEnabled(); expect(await unload(page)).toBe(true);
    await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0); expect(await unload(page)).toBe(false);
    await control.uncheck(); await panel.locator('.top-nav button[data-page="overview"]').click(); await acceptConfirmation(panel);
    await expect(page).toHaveURL(/overview$/);
    expect(await page.evaluate((section) => window.browserHarness.calls.filter((call) => call.section === section && ["update", "save_policy"].includes(call.action)).length, section)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

test("Request Rules settings save atomically and retain drafts through search and immediate toggles", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules")); const panel = panelFor(page);
  await expect(panel.locator(".rule-settings details")).toHaveCount(2);
  await panel.locator(".rule-settings details").evaluateAll((details) => details.forEach((item) => { item.open = true; }));
  await panel.locator("#rules-default-word-forms").uncheck();
  await panel.locator("#wording-add").click();
  await panel.locator(".wording-canonical").fill("turn on"); await panel.locator(".wording-alternatives").fill("enable, switch on");
  await panel.locator("#rule-search").fill("baseline"); await expect(panel.locator("#save-page")).toBeVisible();
  await rejectOnce(page, "request_rules", "settings");
  await panel.locator("#save-page").click();
  await expect(panel.locator("#toast")).toContainText("Save rejected");
  await expect(panel.locator(".wording-canonical")).toHaveValue("turn on");
  await expect(panel.locator("#rules-default-word-forms")).not.toBeChecked();
  expect(await page.evaluate(() => window.browserHarness.getState().requestRules.wording_groups)).toEqual([]);
  await expect(panel.locator("#save-page")).toBeEnabled();
  await panel.locator("#save-page").click(); await expect(panel.locator(".save-bar")).toHaveCount(0);
  expect(await page.evaluate(() => window.browserHarness.getState().requestRules.wording_groups)).toEqual([{canonical: "turn on", alternatives: ["enable", "switch on"]}]);
  await panel.locator("#rules-default-word-forms").check();
  await panel.locator(".rule-enabled").uncheck();
  await expect.poll(() => page.evaluate(() => window.browserHarness.getState().requestRules.rules[0].enabled)).toBe(false);
  await expect(panel.locator("#save-page")).toBeVisible();
  await panel.locator("#discard-page").click();
  await expect(panel.locator("#rules-default-word-forms")).not.toBeChecked();
  await expect(panel.locator(".rule-enabled")).not.toBeChecked();
  await expectHarnessClean(page, errors);
});

test("Request Rule dialog guards X/Escape and unload, while Cancel deliberately discards", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules")); const panel = panelFor(page), dialog = panel.locator("#rule-dialog");
  await panel.locator(".rule-edit").click(); await page.keyboard.press("Escape"); await expect(dialog).not.toBeVisible();
  await panel.locator(".rule-edit").click(); await panel.locator("#rule-name").fill("Changed rule"); expect(await unload(page)).toBe(true);
  await dialog.locator(".icon.rule-close").click(); await panel.locator("#confirm-cancel").click(); await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape"); await acceptConfirmation(panel); await expect(dialog).not.toBeVisible(); expect(await unload(page)).toBe(false);
  await panel.locator(".rule-edit").click(); await panel.locator("#rule-name").fill("Changed again");
  await rejectOnce(page, "request_rules", "update"); await dialog.getByRole("button", {name: "Save", exact: true}).click();
  await expect(panel.locator("#rule-error")).toContainText("Save rejected"); await expect(panel.locator("#rule-save")).toHaveText("Save"); await expect(panel.locator("#rule-name")).toHaveValue("Changed again");
  await dialog.getByRole("button", {name: "Cancel", exact: true}).click(); await expect(dialog).not.toBeVisible();
  await expect(panel.locator("#confirm-dialog")).not.toBeVisible(); expect(await unload(page)).toBe(false);
  await expectHarnessClean(page, errors);
});

for (const [kind, view, open, field, section, action] of [
  ["knowledge", "data-memory/knowledge", "#add-source", "#knowledge-title", "knowledge", "create"],
  ["memory", "data-memory/memories", "#add-memory", "#memory-content", "memories", "add"],
  ["temporary-memory", "data-memory/memories", ".actions .edit-temporary-memory", "#temporary-memory-content", "memories", "temporary_update"],
  ["tool", "capabilities/functions", ".edit-tool", "#tool-yaml", "tools", "save"],
  ["group", "capabilities/functions", ".edit-group", "#group-description", "tools", "save_group"],
]) {
  test(`${kind} editor: common close guard, pending save, retained values and intentional Cancel`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(view)); const panel = panelFor(page);
    if (kind === "temporary-memory") {
      await expect(panel.locator("#add-memory")).toBeVisible();
      await panel.evaluate((host) => {
        const original = host._hass.callWS.bind(host._hass);
        host._hass.callWS = (request) => request.section === "memories" && request.action === "temporary_list"
          ? Promise.resolve({memories: [{memory_id: "temporary-1", content: "Short term fact", category: "general", expires_at: "2027-01-01T12:00:00Z", owner_scope_id: "user:test-user"}]}) : original(request);
      });
      await panel.locator('.memory-kind[data-kind="temporary"]').click();
    }
    const dialog = panel.locator(`#${kind}-dialog`);
    await panel.locator(open).first().evaluate((button) => { for (let parent = button.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true; });
    await panel.locator(open).first().click();
    if (kind === "tool") await expect(panel.locator(field)).toHaveValue(/Baseline browser fixture/);
    await page.keyboard.press("Escape"); await expect(dialog).not.toBeVisible();
    await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
    await panel.locator(open).first().click();
    if (kind === "tool") await expect(panel.locator(field)).toHaveValue(/Baseline browser fixture/);
    const edited = kind === "tool" ? (await panel.locator(field).inputValue()).replace("Baseline browser fixture", "Changed browser fixture") : "Changed editor value";
    await panel.locator(field).fill(edited);
    if (kind === "knowledge") await panel.locator("#knowledge-content").fill("Retain this source content");
    expect(await unload(page)).toBe(true);
    await page.keyboard.press("Escape"); await panel.locator("#confirm-cancel").click();
    await expect(dialog).toBeVisible(); await expect(panel.locator(field)).toHaveValue(edited);
    // An unrelated refresh must not replace a live modified editor.
    await panel.evaluate((host) => { host._eocMainMarkup = null; host._render(); });
    await expect(dialog).toBeVisible(); await expect(panel.locator(field)).toHaveValue(edited);
    await page.evaluate(({section, action}) => {
      const hass = window.browserHarness.hass, original = hass.callWS.bind(hass);
      window.editorSaveCalls = 0;
      hass.callWS = async (request) => {
        if (request.section === section && request.action === action) {
          window.editorSaveCalls++;
          await new Promise((resolve) => { window.releaseEditorSave = resolve; });
          throw Error("Editor save rejected");
        }
        return original(request);
      };
    }, {section, action});
    const save = panel.locator(`#${kind}-save`);
    await save.click(); await expect.poll(() => page.evaluate(() => window.editorSaveCalls)).toBe(1);
    await expect(save).toBeDisabled();
    await dialog.evaluate((element) => {
      const form = element.querySelector("form");
      if (form) form.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
      else element.querySelector('[id$="-save"]').dispatchEvent(new MouseEvent("click", {bubbles: true}));
    });
    expect(await page.evaluate(() => window.editorSaveCalls)).toBe(1);
    await page.evaluate(() => window.releaseEditorSave());
    await expect(panel.locator(`#${kind}-error`)).toContainText("Editor save rejected");
    await expect(save).toBeEnabled(); await expect(save).toHaveText("Save"); await expect(panel.locator(field)).toHaveValue(edited);
    await dialog.getByRole("button", {name: "Cancel", exact: true}).click();
    await expect(dialog).not.toBeVisible(); await expect(panel.locator("#confirm-dialog")).not.toBeVisible();
    expect(await unload(page)).toBe(false);
    await expectHarnessClean(page, errors);
  });
}

for (const [view, field] of [["assistant/basics", '[data-config="__title"]'], ["capabilities/guest-mode", "#guest-controls-enabled"], ["capabilities/quiet-hours", "#qh-enabled"], ["capabilities/request-rules", "#rules-default-word-forms"]]) {
  test(`${view}: agent switching asks before discarding`, async ({page}) => {
    await page.goto(fixtureUrl(view)); const panel = panelFor(page);
    await expect(panel.locator(field)).toBeAttached();
    await panel.evaluate((host) => { host._data.agents.push({...host._data.agents[0], subentry_id: "agent-2", title: "Second assistant"}); host._render(); });
    await panel.locator(field).evaluate((input) => {
      for (let parent = input.parentElement; parent; parent = parent.parentElement) if (parent.tagName === "DETAILS") parent.open = true;
    });
    if (view.startsWith("assistant")) await panel.locator(field).fill("Changed title");
    else await panel.locator(field).setChecked(!(await panel.locator(field).isChecked()));
    await panel.locator("#agent").selectOption("agent-2");
    await expect(panel.locator("#confirm-dialog")).toBeVisible(); await panel.locator("#confirm-cancel").click();
    await expect(panel.locator("#agent")).toHaveValue("agent-1"); expect(await unload(page)).toBe(true);
    await panel.locator("#agent").selectOption("agent-2"); await acceptConfirmation(panel);
    await expect(panel.locator("#agent")).toHaveValue("agent-2"); expect(await unload(page)).toBe(false);
  });
}

test("Quiet Hours time, volume and wake sound return exactly to their baseline", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/quiet-hours")); const panel = panelFor(page);
  for (const [field, changed, original] of [["#qh-start", "23:00", "22:00"], ["#qh-end", "08:00", "07:00"]]) {
    await panel.locator(field).fill(changed); await expect(panel.locator("#save-page")).toBeVisible();
    await panel.locator(field).fill(original); await expect(panel.locator(".save-bar")).toHaveCount(0);
  }
  await panel.locator("#qh-wake").selectOption("unchanged"); await expect(panel.locator("#save-page")).toBeVisible();
  await panel.locator("#qh-wake").selectOption("off"); await expect(panel.locator(".save-bar")).toHaveCount(0);
  for (const value of [40, 20]) {
    await panel.locator("#qh-volume").evaluate((input, value) => { input.value = value; input.dispatchEvent(new Event("input", {bubbles: true})); }, value);
    await expect(panel.locator(".save-bar")).toHaveCount(value === 40 ? 1 : 0);
  }
});

test("an Assistant edit during a pending save remains dirty, and stale saves preserve it", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics")); const panel = panelFor(page), title = panel.locator('[data-config="__title"]');
  await title.fill("Submitted title");
  await page.evaluate(() => {
    const hass = window.browserHarness.hass, original = hass.callWS.bind(hass);
    window.configSaves = 0;
    hass.callWS = async (request) => {
      if (request.section === "configuration" && request.action === "save") {
        window.configSaves++;
        if (window.configSaves === 1) await new Promise((resolve) => { window.releaseConfig = resolve; });
        else throw Error("Configuration changed in another tab. Reload the latest saved settings before saving.");
      }
      return original(request);
    };
  });
  await panel.locator("#save-config").click(); await expect.poll(() => page.evaluate(() => window.configSaves)).toBe(1);
  await title.fill("Later title"); await page.evaluate(() => window.releaseConfig());
  await expect(panel.locator("#save-config")).toBeEnabled(); await expect(panel.locator("#save-config")).toHaveText("Save changes");
  await expect(title).toHaveValue("Later title"); expect(await unload(page)).toBe(true);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Submitted title");
  await panel.locator("#save-config").click(); await expect(panel.locator("#toast")).toContainText("changed in another tab");
  await expect(title).toHaveValue("Later title"); expect(await unload(page)).toBe(true);
});

test("Guest activation remains immediate, coalesces requests, and preserves policy edits", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/guest-mode")); const panel = panelFor(page);
  await panel.locator("#guest-controls-enabled").evaluate((input) => input.closest("details").open = true);
  await panel.locator("#guest-controls-enabled").check();
  await page.evaluate(() => {
    const hass = window.browserHarness.hass, original = hass.callWS.bind(hass);
    window.activations = 0;
    hass.callWS = async (request) => {
      if (request.section === "guest_mode" && request.action === "update") {
        window.activations++;
        await new Promise((resolve) => { window.releaseActivation = resolve; });
      }
      return original(request);
    };
  });
  await panel.locator("#guest-now").evaluate((button) => { button.click(); button.dispatchEvent(new MouseEvent("click", {bubbles: true})); });
  await expect.poll(() => page.evaluate(() => window.activations)).toBe(1);
  await expect(panel.locator("#guest-now")).toBeDisabled(); await page.evaluate(() => window.releaseActivation());
  await expect(panel.locator("#guest-now")).toBeEnabled(); await expect(panel.locator("#guest-controls-enabled")).toBeChecked();
  await expect(panel.locator("#save-page")).toBeVisible();
  expect(await page.evaluate(() => window.browserHarness.getState().guest.config.guest_mode_enabled)).toBe(false);
});

test("Function Tool stale revision rejects save and preserves unsaved YAML", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions")); const panel = panelFor(page);
  const editTool = panel.locator(".edit-tool").first();
  await editTool.evaluate((button) => {
    for (let parent = button.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  });
  await editTool.click();
  const yaml = panel.locator("#tool-yaml");
  await expect(yaml).toBeEditable();
  const edited = (await yaml.inputValue()).replace("Baseline browser fixture", "Unsaved stale editor");
  await yaml.fill(edited);

  const newer = await page.evaluate(async () => {
    const panel = window.browserHarness.panel;
    return window.browserHarness.hass.callWS({
      type: "extended_openai_conversation_responses/management",
      section: "tools",
      action: "set_enabled",
      entry_id: "entry-1",
      subentry_id: "agent-1",
      name: "baseline_tool",
      enabled: false,
      revision: panel._configData.revision,
    });
  });
  expect(typeof newer.revision).toBe("string");

  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-error")).toContainText("changed in another tab");
  await expect(yaml).toHaveValue(edited);
  expect(await unload(page)).toBe(true);

  const state = await page.evaluate(() => window.browserHarness.getState());
  expect(state.configuration.config.functions[0].enabled).toBe(false);
  expect(state.configuration.config.functions[0].spec.description).toBe("Baseline browser fixture Function Tool");
  await expectHarnessClean(page, errors);
});

test("Function Group stale revision rejects save and preserves unsaved fields", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions")); const panel = panelFor(page);
  const editGroup = panel.locator(".edit-group").first();
  await editGroup.evaluate((button) => {
    for (let parent = button.parentElement; parent; parent = parent.parentElement) {
      if (parent.tagName === "DETAILS") parent.open = true;
    }
  });
  await editGroup.click();
  const description = panel.locator("#group-description");
  await description.fill("Unsaved stale group description");

  const newer = await page.evaluate(async () => {
    const panel = window.browserHarness.panel;
    const state = window.browserHarness.getState();
    const group = structuredClone(state.configuration.config.function_groups[0]);
    group.description = "Newer external group description";
    return window.browserHarness.hass.callWS({
      type: "extended_openai_conversation_responses/management",
      section: "tools",
      action: "save_group",
      entry_id: "entry-1",
      subentry_id: "agent-1",
      original_id: group.id,
      group,
      revision: panel._configData.revision,
    });
  });
  expect(typeof newer.revision).toBe("string");

  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-error")).toContainText("changed in another tab");
  await expect(description).toHaveValue("Unsaved stale group description");
  expect(await unload(page)).toBe(true);

  const state = await page.evaluate(() => window.browserHarness.getState());
  expect(state.configuration.config.function_groups[0].description).toBe("Newer external group description");
  await expectHarnessClean(page, errors);
});

