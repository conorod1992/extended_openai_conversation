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
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    await replacePanel(page);
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    await panel.locator("#save-config").click();
    await expect(panel.locator("#dirty-state")).toBeHidden();
    await replacePanel(page);
    await expect(panel.locator('[data-config="prompt"]')).toHaveValue("Long unsaved private prompt");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
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
