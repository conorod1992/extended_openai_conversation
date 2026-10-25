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

async function holdToolRecovery(panel, marker) {
  await panel.evaluate((host, marker) => {
    const textarea = host.shadowRoot.querySelector("#tool-yaml");
    textarea.value += `\n# ${marker}\n`;
    textarea.dispatchEvent(new Event("input", {bubbles:true}));
    window.__originalEditorPanel = host;
    const call = host.hass.callWS.bind(host.hass);
    const gate = new Promise(resolve => {window.__releaseEditorRecovery = resolve;});
    let held = false;
    host.hass.callWS = async message => {
      if (!held && message.section === "tools" && message.action === "serialize") {
        held = true;
        window.__editorRecoveryWaiting = true;
        await gate;
      }
      return call(message);
    };
  }, marker);
}

for (const bundled of [false, true]) {
  for (const closeMethod of ["Cancel", "Escape"]) test(`Knowledge recovery respects ${closeMethod} after refresh (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("data-memory/knowledge", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await identify(page);
    await panel.locator("#add-source").click();
    await panel.locator("#knowledge-title").fill("Saved Knowledge source");
    await panel.locator("#knowledge-content").fill("Saved Knowledge content");
    await panel.locator("#knowledge-save").click();
    await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
    await panel.locator(".source-edit-button").first().click();
    await expect(panel.locator("#knowledge-content")).toBeEnabled();
    await panel.locator("#knowledge-content").fill("Keep this deferred Knowledge draft");
    await panel.evaluate(host => {
      window.knowledgeRecoverySource = host;
      const call = host.hass.callWS.bind(host.hass);
      const gate = new Promise(resolve => {window.releaseKnowledgeRecovery = resolve;});
      let held = false;
      host.hass.callWS = async message => {
        if (!held && message.section === "knowledge" && message.action === "get") {
          held = true;
          window.knowledgeRecoveryWaiting = true;
          await gate;
        }
        return call(message);
      };
    });
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.knowledgeRecoveryWaiting))).toBe(true);
    await panel.evaluate(host => host._loadSection(true));
    if (closeMethod === "Cancel") await panel.locator("#knowledge-dialog .close-editor").filter({hasText:"Cancel"}).click();
    else await page.keyboard.press("Escape");
    await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
    await page.evaluate(() => window.releaseKnowledgeRecovery());
    await expect.poll(() => page.evaluate(() => Boolean(window.knowledgeRecoverySource._eocRecoveringEditor))).toBe(false);
    await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
    expect(await page.evaluate(() => window.knowledgeRecoverySource.shadowRoot.querySelector("#knowledge-dialog").open)).toBe(true);
    await panel.evaluate(host => host._navigate("guide"));
    await panel.evaluate(host => host._navigate("data-memory", "knowledge"));
    await expect(panel.locator("#knowledge-content")).toHaveValue("Keep this deferred Knowledge draft");
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "knowledge" && call.action === "update").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`cancel after refresh defers Function recovery until a later visit (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await identify(page);
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await holdToolRecovery(panel, "Draft deferred after refresh and Cancel");
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__editorRecoveryWaiting))).toBe(true);
    await panel.evaluate(host => host._loadSection(true));
    await panel.locator("#tool-cancel").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await page.evaluate(() => window.__releaseEditorRecovery());
    await expect.poll(() => page.evaluate(() => Boolean(window.__originalEditorPanel._eocRecoveringEditor))).toBe(false);
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    expect(await page.evaluate(() => window.__originalEditorPanel.shadowRoot.querySelector("#tool-dialog").open)).toBe(true);
    await panel.evaluate(host => host._navigate("guide"));
    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(/Draft deferred after refresh and Cancel/);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`refresh during Function recovery keeps the retained draft reachable (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await identify(page);
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await holdToolRecovery(panel, "Draft retained through an in-place refresh");
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__editorRecoveryWaiting))).toBe(true);
    await panel.evaluate(host => host._loadSection(true));
    await page.evaluate(() => window.__releaseEditorRecovery());
    await expect.poll(() => page.evaluate(() => Boolean(window.__originalEditorPanel._eocRecoveringEditor))).toBe(false);
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(/Draft retained through an in-place refresh/);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`an unavailable unsaved Rule choice is retained for review (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-edit").first()).toBeVisible();
    await identify(page);
    await panel.evaluate(async host => {
      await host._call("request_rules", "groups", {groups:[{id:"unsaved-destination", name:"Unsaved destination"}], revision:host._result.revision});
      await host._loadSection(true);
    });
    await panel.locator(".rule-edit").first().click();
    await panel.locator("#rule-name").fill("Preserve this entire Rule draft");
    await panel.locator("#rule-group").selectOption("unsaved-destination");
    await panel.evaluate(async host => {
      window.__ruleDraftSource = host;
      const agent = host._selectedAgent();
      await host.hass.callWS({type:"extended_openai_conversation_responses/management", section:"request_rules", action:"groups",
        entry_id:agent.entry_id, subentry_id:agent.subentry_id, groups:[], revision:host._result.revision});
    });
    await replacePanel(page);
    await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toBeVisible();
    await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/unsaved-destination/);
    await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/Preserve this entire Rule draft/);
    expect(await page.evaluate(() => window.__ruleDraftSource.shadowRoot.querySelector("#rule-dialog").open)).toBe(true);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`returning to Functions while recovery awaits restores the original draft (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await identify(page);
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await holdToolRecovery(panel, "Original draft after returning to Functions");
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__editorRecoveryWaiting))).toBe(true);
    await panel.evaluate(host => host._navigate("guide"));
    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    await expect(panel.locator("#function-add")).toBeVisible();
    await page.evaluate(() => window.__releaseEditorRecovery());
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(/Original draft after returning to Functions/);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`cancelled Knowledge recovery preserves a newer editor (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("data-memory/knowledge", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await identify(page);
    await panel.locator("#add-source").click();
    await panel.locator("#knowledge-title").fill("Saved recovery source");
    await panel.locator("#knowledge-content").fill("Saved Knowledge content");
    await panel.locator("#knowledge-save").click();
    await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", false);
    await expect(panel.locator(".source-edit-button").first()).toBeVisible();
    await panel.locator(".source-edit-button").first().click();
    await expect(panel.locator("#knowledge-content")).toBeEnabled();
    await panel.locator("#knowledge-content").fill("Original retained Knowledge draft");
    await panel.evaluate(host => {
      window.__knowledgeSource = host;
      const call = host.hass.callWS.bind(host.hass);
      const gate = new Promise(resolve => {window.__releaseKnowledgeRecovery = resolve;});
      let held = false;
      host.hass.callWS = async message => {
        if (!held && message.section === "knowledge" && message.action === "get") {
          held = true;
          window.__knowledgeRecoveryWaiting = true;
          await gate;
        }
        return call(message);
      };
    });
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__knowledgeRecoveryWaiting))).toBe(true);
    await panel.locator("#knowledge-dialog .close-editor").filter({hasText:"Cancel"}).click();
    await panel.locator(".source-edit-button").first().click();
    await expect(panel.locator("#knowledge-content")).toBeEnabled();
    await panel.locator("#knowledge-content").fill("New manually authored Knowledge draft");
    await page.evaluate(() => window.__releaseKnowledgeRecovery());
    await expect.poll(() => page.evaluate(() => Boolean(window.__knowledgeSource._eocRecoveringEditor))).toBe(false);
    await expect(panel.locator("#knowledge-content")).toHaveValue("New manually authored Knowledge draft");
    expect(await page.evaluate(() => window.__knowledgeSource.shadowRoot.querySelector("#knowledge-dialog").open)).toBe(true);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "knowledge" && call.action === "update").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`Rule editor stays protected after switching assistants (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/request-rules", `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".rule-edit").first()).toBeVisible();
    await identify(page);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("scale-agent-1");
    await panel.locator(".rule-edit").first().click();
    await panel.locator("#rule-name").fill("Rule draft after switching assistants");
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    expect(await page.evaluate(() => !window.dispatchEvent(new Event("beforeunload", {cancelable:true})))).toBe(true);
    await panel.evaluate(host => {host._confirm = async () => false;});
    await panel.locator("#agent").selectOption("agent-1");
    expect(await panel.evaluate(host => host._agentId)).toBe("scale-agent-1");
    await replacePanel(page);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#rule-name")).toHaveValue("Rule draft after switching assistants");
    expect(await panel.locator("#agent").inputValue()).toBe("scale-agent-1");
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    const saves = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update"));
    expect(saves).toHaveLength(1);
    expect(saves[0].subentry_id).toBe("scale-agent-1");
    await expectHarnessClean(page, errors);
  });

  test(`Function recovery follows a second replacement during asynchronous preparation (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await identify(page);
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await holdToolRecovery(panel, "Original draft through two replacements");
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__editorRecoveryWaiting))).toBe(true);
    await replacePanel(page);
    await expect(panel.locator("#function-add")).toBeVisible();
    await page.evaluate(() => window.__releaseEditorRecovery());
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
    await expect(panel.locator("#tool-yaml")).toHaveValue(/Original draft through two replacements/);
    expect(await page.evaluate(() => window.__originalEditorPanel.shadowRoot.querySelector("#tool-dialog").open)).toBe(false);
    await panel.locator("#tool-save").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    const saves = await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save"));
    expect(saves).toHaveLength(1);
    await replacePanel(page);
    await expect(panel.locator("#function-add")).toBeVisible();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await expectHarnessClean(page, errors);
  });

  test(`cancelled Function recovery cannot overwrite a new editor (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.getByText("Show member functions", {exact:true}).click();
    await identify(page);
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await holdToolRecovery(panel, "Original retained Function draft");
    await replacePanel(page);
    await expect.poll(() => page.evaluate(() => Boolean(window.__editorRecoveryWaiting))).toBe(true);
    await panel.locator("#tool-cancel").click();
    const members = panel.getByText("Show member functions", {exact:true});
    if (await members.isVisible()) await members.click();
    await panel.locator(".edit-tool").first().click();
    await expect.poll(() => panel.evaluate(host => typeof host._toolInitialYaml)).toBe("string");
    await panel.locator("#tool-yaml").evaluate(textarea => {
      textarea.value += "\n# New manually authored Function draft\n";
      textarea.dispatchEvent(new Event("input", {bubbles:true}));
    });
    await page.evaluate(() => window.__releaseEditorRecovery());
    await expect.poll(() => page.evaluate(() => Boolean(window.__originalEditorPanel._eocRecoveringEditor))).toBe(false);
    await expect(panel.locator("#tool-yaml")).toHaveValue(/New manually authored Function draft/);
    expect(await page.evaluate(() => window.__originalEditorPanel.shadowRoot.querySelector("#tool-dialog").open)).toBe(true);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length)).toBe(0);
    await panel.locator("#tool-save").click();
    await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
    await replacePanel(page);
    await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toBeVisible();
    await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/Original retained Function draft/);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "tools" && call.action === "save").length)).toBe(1);
    await expectHarnessClean(page, errors);
  });
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
    const wrapper=document.createElement("ha-selector-action"); root.append(wrapper);
    const collection=document.createElement("ha-automation-action"); wrapper.attachShadow({mode:"open"}).append(collection);
    const yaml=document.createElement("ha-yaml-editor"); collection.attachShadow({mode:"open"}).append(yaml);
    yaml.yaml="- event: [invalid";
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
  });
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
  const actions=[{event:"intended_corrected_event", event_data:{message:"Intended new actions"}}];
  await selector.evaluate((element, actions) => {
    const collection=element.shadowRoot.querySelector("ha-selector-action").shadowRoot.querySelector("ha-automation-action");
    const yaml=collection.shadowRoot.querySelector("ha-yaml-editor");
    yaml.yaml="- event: intended_corrected_event";
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:true, value:actions}, bubbles:true, composed:true}));
  }, actions);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML"); // Valid but not yet applied by the selector.
  await selector.evaluate((element, actions) => {
    const collection=element.shadowRoot.querySelector("ha-selector-action").shadowRoot.querySelector("ha-automation-action");
    collection.dispatchEvent(new CustomEvent("value-changed", {detail:{value:actions}, bubbles:true, composed:true}));
    const yaml=collection.shadowRoot.querySelector("ha-yaml-editor");
    yaml.codemirror={state:{doc:{toString:()=>yaml.yaml}}};
    yaml.dispatchEvent(new Event("input", {bubbles:true, composed:true}));
  }, actions);
  await expect.poll(()=>selector.evaluate(element=>element.__eocYamlDraft()[0]?.pending)).toBe(false);
  await selector.evaluate(element => {
    const collection=element.shadowRoot.querySelector("ha-selector-action").shadowRoot.querySelector("ha-automation-action");
    const yaml=collection.shadowRoot.querySelector("ha-yaml-editor");
    yaml.codemirror={state:{doc:{toString:()=>"- event: still_unparsed"}}};
    yaml.dispatchEvent(new Event("input", {bubbles:true, composed:true}));
  });
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
  await selector.evaluate((element, actions) => {
    const collection=element.shadowRoot.querySelector("ha-selector-action").shadowRoot.querySelector("ha-automation-action");
    const yaml=collection.shadowRoot.querySelector("ha-yaml-editor");
    yaml.codemirror={state:{doc:{toString:()=>yaml.yaml}}};
    yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:true, value:actions}, bubbles:true, composed:true}));
    collection.dispatchEvent(new CustomEvent("value-changed", {detail:{value:actions}, bubbles:true, composed:true}));
  }, actions);
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => window.browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update").at(-1).rule.action.actions)).toEqual(actions);
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
});


for (const kind of ["action", "condition"]) {
  test(`deleting invalid nested ${kind} YAML preserves other edits and permits Save`, async ({page}) => {
    await page.goto(fixtureUrl("capabilities/request-rules"));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator(".rule-edit").first().click();
    await panel.locator("#rule-action-type").selectOption("local_action");
    await panel.locator("#rule-name").fill("Keep my other unsaved edits");
    const actions=[{event:"removed_action"}, {event:"retained_action"}];
    const conditions=[{condition:"state", entity_id:"light.first", state:"on"}, {condition:"state", entity_id:"light.second", state:"on"}];
    await panel.locator("#rule-action-sequence-host ha-selector").evaluate((element, value) => {
      element.value=value;
      element.dispatchEvent(new CustomEvent("value-changed", {detail:{value}, bubbles:true, composed:true}));
    }, actions);
    await panel.locator("#rule-condition-host ha-selector").evaluate((element, value) => {
      element.value=value;
      element.dispatchEvent(new CustomEvent("value-changed", {detail:{value}, bubbles:true, composed:true}));
    }, conditions);
    const selector=panel.locator(kind === "action" ? "#rule-action-sequence-host ha-selector" : "#rule-condition-host ha-selector");
    await selector.evaluate(element => {
      const root=element.shadowRoot || element.attachShadow({mode:"open"});
      const wrapper=document.createElement("div"); wrapper.dataset.testWrapper=""; root.append(wrapper);
      const nested=wrapper.attachShadow({mode:"open"});
      const action=document.createElement("div"); action.dataset.testDeleted=""; nested.append(action);
      const editorRoot=action.attachShadow({mode:"open"});
      editorRoot.append(document.createElement("ha-yaml-editor"));
    });
    await selector.evaluate(element => {
      const wrapper=element.shadowRoot.querySelector("[data-test-wrapper]");
      const yaml=wrapper.shadowRoot.querySelector("[data-test-deleted]").shadowRoot.querySelector("ha-yaml-editor");
      yaml.yaml="- invalid: [unfinished";
      yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
    });
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
    expect(await selector.evaluate(element => element.__eocYamlDraft())).toEqual([{raw:"- invalid: [unfinished", valid:false, pending:false}]);
    await selector.evaluate(element => element.dispatchEvent(new CustomEvent("value-changed", {detail:{value:element.value}, bubbles:true, composed:true})));
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-error")).toContainText("Correct the YAML");
    const remaining=(kind === "action" ? actions : conditions).slice(1);
    await selector.evaluate((element, value) => {
      element.shadowRoot.querySelector("[data-test-wrapper]").shadowRoot.querySelector("[data-test-deleted]").remove();
      element.value=value;
      element.dispatchEvent(new CustomEvent("value-changed", {detail:{value}, bubbles:true, composed:true}));
    }, remaining);
    expect(await selector.evaluate(element => element.__eocYamlDraft())).toEqual([]);
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    const saved=await page.evaluate(() => window.browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update").at(-1).rule);
    expect(saved.name).toBe("Keep my other unsaved edits");
    expect(kind === "action" ? saved.action.actions : saved.conditions).toEqual(remaining);
  });
}


for (const bundled of [false, true]) {
  test(`recovered policy draft keeps its original revision (${bundled})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/quiet-hours", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator("#qh-start")).toBeVisible();
    await identify(page);
    await panel.evaluate(async host => {
      const call = host.hass.callWS.bind(host.hass);
      window.__policyServer = {revision:"old-policy", updates:[]};
      host.hass.callWS = async message => {
        const server = window.__policyServer;
        if (message.section === "quiet_hours" && message.action === "update") {
          server.updates.push(structuredClone(message));
          if (message.revision !== server.revision) throw new Error("Quiet Hours configuration changed; reload before saving");
        }
        const result = await call(message);
        return message.section === "quiet_hours" ? {...result, revision:server.revision,
          config:{...result.config, ...(server.revision === "new-policy" ? {end:"04:02"} : {})}} : result;
      };
      host._eocSectionCache?.clear?.();
      await host._loadSection(true);
    });
    await panel.locator("#qh-start").fill("23:17");
    await panel.locator("#qh-start").dispatchEvent("change");
    await page.evaluate(() => {window.__policyServer.revision = "new-policy";});
    await replacePanel(page);
    await expect(panel.locator("#qh-start")).toHaveValue("23:17");
    await panel.locator("#save-page").click();
    await expect.poll(() => page.evaluate(() => window.__policyServer.updates.length)).toBe(1);
    expect(await page.evaluate(() => window.__policyServer.updates[0].revision)).toBe("old-policy");
    await expect(panel.locator("#toast")).toContainText("reload before saving");
    await expect(panel.locator("#save-page")).toBeVisible();
    await panel.locator("#discard-page").click();
    await expect(panel.locator("#qh-end")).toHaveValue("04:02");
    await panel.locator("#qh-start").fill("23:18");
    await panel.locator("#qh-start").dispatchEvent("change");
    await panel.locator("#save-page").click();
    await expect.poll(() => page.evaluate(() => window.__policyServer.updates.length)).toBe(2);
    expect(await page.evaluate(() => window.__policyServer.updates[1].revision)).toBe("new-policy");
    await expect(panel.locator("#save-page")).toHaveCount(0);
    await expectHarnessClean(page, errors);
  });
}
