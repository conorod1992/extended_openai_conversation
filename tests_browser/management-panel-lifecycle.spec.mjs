import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

async function identify(page, id = "admin") {
  await page.locator("extended-openai-management-panel").evaluate((panel, id) => {
    panel.hass = {...panel.hass, user:{id, is_admin:true}};
  }, id);
}

async function replacePanel(page, user = "admin") {
  await page.evaluate(user => {
    const old = document.querySelector("extended-openai-management-panel");
    const parent = old.parentElement;
    const hass = {...old.hass, user:{id:user, is_admin:true}};
    old.remove(); // Runs the real disconnectedCallback, as HA's wrapper does.
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  }, user);
}

for (const navigation of ["sidebar", "back-forward", "suspension"]) {
  test(`unsaved prompt survives ${navigation} panel replacement`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/prompt-context"));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="prompt"]')).toBeVisible();
    await identify(page);
    await panel.locator('[data-config="prompt"]').fill("Long unsaved private prompt");
    if (navigation !== "suspension") await page.evaluate(() => {
      window.__returnPath = location.pathname;
      history.pushState(null, "", "/lovelace/0");
      window.dispatchEvent(new CustomEvent("location-changed"));
    });
    // An external HA route owns teardown; the integration does not cancel HA's
    // navigation or manufacture an unload event for an in-document transition.
    await page.evaluate(async navigation => {
      const old = document.querySelector("extended-openai-management-panel");
      window.__oldPanel = old;
      old.remove();
      window.__outsideUnloadProtected = !window.dispatchEvent(new Event("beforeunload", {cancelable:true}));
      if (navigation === "back-forward") {
        const traverse = action => new Promise(resolve => {
          window.addEventListener("popstate", resolve, {once:true});
          history[action]();
        });
        await traverse("back");
        await traverse("forward");
        await traverse("back");
      } else history.replaceState(null, "", window.__returnPath || location.pathname);
      document.body.append(old);
    }, navigation);
    expect(await page.evaluate(() => window.__outsideUnloadProtected)).toBe(true);
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    await replacePanel(page);
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    await panel.locator("#save-config").click();
    await expect(panel.locator("#dirty-state")).toBeHidden();
    await replacePanel(page);
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable:true})))).toBe(false);
    await expectHarnessClean(page, errors);
  });
}

test("draft recovery does not cross users", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await identify(page);
  await panel.locator('[data-config="__title"]').fill("Private admin draft");
  await replacePanel(page, "other-admin");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await replacePanel(page, "admin");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Private admin draft");
  await expectHarnessClean(page, errors);
});

test("draft recovery does not cross agents", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await identify(page);
  await panel.locator('[data-config="__title"]').fill("Only agent one's draft");
  await page.evaluate(() => localStorage.setItem("extended-openai-agent", "scale-agent-1"));
  await replacePanel(page);
  await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await panel.locator("#agent").selectOption("agent-1");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Only agent one's draft");
  await panel.locator("#revert-config").click();
  await replacePanel(page);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expectHarnessClean(page, errors);
});

test("recovery preserves edits while adopting fresh unchanged configuration", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await identify(page);
  await panel.locator('[data-config="__title"]').fill("Unsaved local name");
  await page.evaluate(async () => {
    const state = browserHarness.getState();
    await browserHarness.hass.callWS({
      type:"extended_openai_conversation_responses/management", section:"configuration", action:"save",
      revision:state.configuration.revision, config:{prompt:"New saved prompt from another client"},
    });
  });
  await replacePanel(page);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Unsaved local name");
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator('[data-config="prompt"]')).toHaveValue("New saved prompt from another client");
  expect(await panel.evaluate(host => host._draftTitle)).toBe("Unsaved local name");
  await expectHarnessClean(page, errors);
});

test("an unvisited policy draft survives returning through another page", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/quiet-hours"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#qh-start")).toBeVisible();
  await identify(page);
  await panel.locator("#qh-start").fill("23:17");
  await panel.locator("#qh-start").dispatchEvent("change");
  await expect(panel.locator("#save-page")).toBeVisible();
  await page.evaluate(() => history.replaceState(null, "", "/extended-openai/assistant/basics"));
  await replacePanel(page);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable:true})))).toBe(true);
  await replacePanel(page);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(host => host._navigate("capabilities", "quiet-hours"));
  await expect(panel.locator("#qh-start")).toHaveValue("23:17");
  await expectHarnessClean(page, errors);
});

test("page policy draft survives panel replacement and explicit discard", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/quiet-hours"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#qh-start")).toBeVisible();
  const saved = await panel.locator("#qh-start").inputValue();
  await identify(page);
  await panel.locator("#qh-start").fill("23:17");
  await panel.locator("#qh-start").dispatchEvent("change");
  await expect(panel.locator("#save-page")).toBeVisible();
  await replacePanel(page);
  await expect(panel.locator("#qh-start")).toHaveValue("23:17");
  await panel.locator("#discard-page").click();
  await replacePanel(page);
  await expect(panel.locator("#qh-start")).toHaveValue(saved);
  await expectHarnessClean(page, errors);
});

for (const kind of ["rule", "memory", "knowledge"]) {
  test(`${kind} editor survives panel replacement`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(kind === "rule" ? "capabilities/request-rules" : `data-memory/${kind === "memory" ? "memories" : "knowledge"}`));
    const panel = page.locator("extended-openai-management-panel");
    const add = kind === "rule" ? panel.getByRole("button", {name:"Create rule", exact:true}).first() : panel.locator(kind === "memory" ? "#add-memory" : "#add-source");
    await expect(add).toBeVisible();
    await identify(page);
    await add.click();
    const input = panel.locator({rule:"#rule-name", memory:"#memory-content", knowledge:"#knowledge-title"}[kind]);
    await input.fill("Unfinished private editor draft");
    await replacePanel(page);
    await expect(panel.locator(`#${kind}-dialog`)).toHaveJSProperty("open", true);
    await expect(input).toHaveValue("Unfinished private editor draft");
    await expectHarnessClean(page, errors);
  });
}

for (const kind of ["group", "tool"]) {
  test(`${kind} editor survives panel replacement`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions"));
    const panel = page.locator("extended-openai-management-panel");
    if (kind === "tool") await panel.getByText("Show member functions", {exact:true}).click();
    await expect(panel.locator(`.edit-${kind}`).first()).toBeVisible();
    await identify(page);
    await panel.locator(`.edit-${kind}`).first().click();
    await expect(panel.locator(`#${kind}-dialog`)).toHaveJSProperty("open", true);
    if (kind === "group") await panel.locator("#group-description").fill("Unfinished group explanation");
    else {
      await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
      await panel.evaluate(host => {
        const editor = host.shadowRoot.querySelector("#tool-yaml");
        editor.value += "\n# Unfinished private tool edit\n";
        editor.dispatchEvent(new Event("input", {bubbles:true}));
      });
    }
    await replacePanel(page);
    await expect(panel.locator(`#${kind}-dialog`)).toHaveJSProperty("open", true);
    if (kind === "group") await expect(panel.locator("#group-description")).toHaveValue("Unfinished group explanation");
    else await expect(panel.locator("#tool-yaml")).toHaveValue(/Unfinished private tool edit/);
    await expectHarnessClean(page, errors);
  });
}

test("a changed saved editor baseline preserves recovered text for review", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".memory-edit-button").first()).toBeVisible();
  await identify(page);
  await panel.locator(".memory-edit-button").first().click();
  await panel.locator("#memory-content").fill("Unfinished local memory edit");
  await panel.evaluate(async host => {
    const memory = host._editingMemory;
    await host.hass.callWS({type:"extended_openai_conversation_responses/management", section:"memories", action:"update",
      scope_id:host._scopeId, memory_id:memory.memory_id, content:"New server-side memory"});
  });
  await replacePanel(page);
  await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toBeVisible();
  await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/Unfinished local memory edit/);
  await panel.getByRole("button", {name:"Discard recovered edits", exact:true}).click();
  await replacePanel(page);
  await expect(panel.locator(".memory-edit-button").first()).toBeVisible();
  await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toHaveCount(0);
  await panel.locator(".memory-edit-button").first().click();
  await expect(panel.locator("#memory-content")).toHaveValue("New server-side memory");
  await expectHarnessClean(page, errors);
});

test("a save acknowledged after teardown is not restored as a dirty draft", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await identify(page);
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    const gate = new Promise(resolve => {window.__releaseSave = resolve;});
    host.hass.callWS = async message => {
      if (message.section === "configuration" && message.action === "save") {window.__saveStarted = true; await gate;}
      return call(message);
    };
  });
  await panel.locator('[data-config="__title"]').fill("Acknowledged during navigation");
  await panel.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => Boolean(window.__saveStarted))).toBe(true);
  await replacePanel(page);
  await page.evaluate(() => window.__releaseSave());
  await expect.poll(() => panel.evaluate(host => Boolean(host._eocDraftRecoveryTimer))).toBe(false);
  // A fresh read is still required: recovery must never manufacture an unsaved
  // copy of a mutation that has already succeeded on the server.
  await replacePanel(page);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Acknowledged during navigation");
  expect(await panel.evaluate(host => host._configDirty)).toBe(false);
  await expectHarnessClean(page, errors);
});


test("bulk repair keeps raw invalid drafts across routes and panel replacement", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  await identify(page);
  await panel.evaluate(async host => {
    const call = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      const result = await call(message);
      if (message.section === "configuration" && message.action === "get") {
        result.function_repair = {isolatable:false, tools_valid:false, validation_error:"Invalid YAML", tools:"- spec: [broken"};
      }
      return result;
    };
    host._invalidateCleanConfiguration(host._agentId);
    host._configData = null;
    host._eocSectionCache?.clear?.();
    await host._loadSection(true);
  });
  const raw = "- spec: [unfinished repair\n  important: retain this text";
  await panel.locator("#function-repair-editor").fill(raw);
  await panel.evaluate(host => host._navigate("guide"));
  await panel.evaluate(host => host._navigate("capabilities", "functions"));
  await expect(panel.locator("#function-repair-editor")).toHaveValue(raw);
  await replacePanel(page);
  await expect(panel.locator("#function-repair-editor")).toHaveValue(raw);
  expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable:true})))).toBe(true);
  await panel.locator("#function-repair-discard").click();
  await expect(panel.locator("#function-repair-editor")).toHaveValue("- spec: [broken");
  await replacePanel(page);
  await expect(panel.locator("#function-repair-editor")).toHaveValue("- spec: [broken");
});

test("HA tool chooser retains filtered selections by identity and selected group", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  await identify(page);
  await panel.evaluate(host => {
    const call = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      if (message.section === "tools" && message.action === "ha_catalog") return {tools:["Alpha", "Beta"].map(name => ({name, description:name, source:name, reference:{source_id:name, api_id:name, tool_name:name}}))};
      if (message.section === "tools" && message.action === "ha_add") {
        window.__addedReferences = message.tools;
        return call({...message, section:"configuration", action:"get"});
      }
      return call(message);
    };
  });
  await panel.locator("#function-add").click();
  await panel.locator("#add-ha-tools").click();
  let dialog = panel.locator("#ha-llm-tools-dialog");
  await dialog.locator('[data-search]').fill("Alpha");
  await dialog.locator('input[type="checkbox"]').check();
  await dialog.locator('[data-search]').fill("Beta");
  await dialog.locator('input[type="checkbox"]').check();
  await dialog.locator('[data-group]').evaluate(select => { const option=new Option("Retained group", "retained-group"); select.append(option); select.value=option.value; select.dispatchEvent(new Event("change")); });
  await replacePanel(page);
  dialog = panel.locator("#ha-llm-tools-dialog");
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(dialog.locator('input[type="checkbox"]:checked')).toHaveCount(2);
  await expect(dialog.locator('[data-group]')).toHaveValue("retained-group");
  await expect(dialog.locator('[data-add]')).toBeDisabled(); // Missing group needs review.
  await dialog.locator('[data-group]').selectOption("");
  await dialog.locator('[data-add]').click();
  await expect(panel.locator("#ha-llm-tools-dialog")).toHaveCount(0);
  expect(await page.evaluate(() => window.__addedReferences.map(tool => tool.tool_name))).toEqual(["Alpha", "Beta"]);
  await replacePanel(page);
  await expect(panel.locator("#ha-llm-tools-dialog")).toHaveCount(0);
});

test("invalid native Rule YAML blocks stale saves, marks dirty and survives replacement", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".rule-edit").first()).toBeVisible();
  await identify(page);
  await panel.locator(".rule-edit").first().click();
  await panel.locator("#rule-action-type").selectOption("local_action");
  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  await selector.evaluate(element => {
    element.value = [{event:"before_edit"}];
    element.dispatchEvent(new CustomEvent("value-changed", {detail:{value:element.value}, bubbles:true}));
  });
  await panel.evaluate(host => host._captureDialogBaseline(host.shadowRoot.querySelector("#rule-dialog")));
  await selector.evaluate(element => {
    const root=element.shadowRoot || element.attachShadow({mode:"open"});
    const yaml=document.createElement("ha-yaml-editor"); root.append(yaml);
  });
  await selector.evaluate(element => {
    const yaml=element.shadowRoot.querySelector("ha-yaml-editor");
    yaml.yaml="- action: light.turn_on\n  data: [invalid";
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
  });
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
  await panel.evaluate(host => { host._confirm = async () => false; });
  await page.keyboard.press("Escape");
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  // Reject the discard decision, preserving the editor's raw text.
  await panel.evaluate(host => host.shadowRoot.querySelector("#confirm-dialog")?.close());
  await replacePanel(page);
  await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toBeVisible();
  expect(await panel.locator('[aria-label="Recovered editor values"]').inputValue()).toContain("data: [invalid");
});


test("correcting native Rule YAML saves the intended actions and clears the draft", async ({page}) => {
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".rule-edit").first().click();
  await panel.locator("#rule-action-type").selectOption("local_action");
  const selector=panel.locator("#rule-action-sequence-host ha-selector");
  await selector.evaluate(element => {
    const root=element.shadowRoot || element.attachShadow({mode:"open"});
    const yaml=document.createElement("ha-yaml-editor"); root.append(yaml);
    yaml.yaml="- event: [invalid";
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
  });
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
  const actions=[{event:"intended_corrected_event", event_data:{message:"Intended new actions"}}];
  await selector.evaluate((element, actions) => {
    const yaml=element.shadowRoot.querySelector("ha-yaml-editor");
    yaml.yaml="- event: intended_corrected_event";
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:true, value:actions}, bubbles:true, composed:true}));
  }, actions);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML"); // Valid but not yet applied by the selector.
  await selector.evaluate((element, actions) => {
    element.value=actions;
    element.dispatchEvent(new CustomEvent("value-changed", {detail:{value:actions}, bubbles:true, composed:true}));
  }, actions);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => window.browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update").at(-1).rule.action.actions)).toEqual(actions);
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
});
