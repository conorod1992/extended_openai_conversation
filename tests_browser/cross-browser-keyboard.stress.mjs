import {expect, test} from "@playwright/test";
import {browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function focused(locator) {
  return locator.evaluate((node) => node.matches(":focus"));
}

async function assertUsableFocus(page, allowBody = false) {
  const state = async () => page.evaluate(() => {
    let node = document.activeElement;
    while (node?.shadowRoot?.activeElement) node = node.shadowRoot.activeElement;
    if (!node || node === document.body) return {usable: false, reason: "body focus"};
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    return {
      usable: !node.disabled && style.visibility !== "hidden" && style.display !== "none"
        && box.width > 0 && box.height > 0 && box.right > 0 && box.bottom > 0
        && box.left < innerWidth && box.top < innerHeight,
      reason: node.outerHTML.slice(0, 180),
    };
  });
  const current = await state();
  if (allowBody && current.reason === "body focus") return;
  await expect.poll(async () => (await state()).usable, {message: current.reason, timeout: 1500}).toBe(true);
}

async function tabTo(page, target, limit = 120) {
  const visited = [];
  for (let index = 0; index < limit; index++) {
    if (await focused(target).catch(() => false)) return;
    await page.keyboard.press("Tab");
    visited.push(await page.evaluate(() => {
      const root = document.querySelector("extended-openai-management-panel")?.shadowRoot;
      const active = root?.activeElement;
      return `${active?.localName || "body"}#${active?.id || ""}`;
    }));
    await assertUsableFocus(page, true);
  }
  throw new Error(`Keyboard could not reach ${target} within ${limit} Tabs: ${JSON.stringify({first: visited.slice(0, 20), last: visited.slice(-20)})}`);
}

test("keyboard-only Memory journey creates, cancels, edits and deletes with usable focus", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  const operations = [];
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  const create = panel.locator("#add-memory");
  await tabTo(page, create);
  await page.keyboard.press("Enter");
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#memory-content"));
  await page.keyboard.type("Keyboard draft cancelled");
  await page.keyboard.press("Escape");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#confirm-accept"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#memory-dialog")).not.toBeVisible();
  await assertUsableFocus(page);
  operations.push("cancelled draft");

  await tabTo(page, create);
  await page.keyboard.press("Space");
  await tabTo(page, panel.locator("#memory-content"));
  await page.keyboard.type("Keyboard journey memory");
  await tabTo(page, panel.locator("#memory-category"));
  await page.keyboard.type("accessibility");
  await tabTo(page, panel.locator("#memory-save"));
  await page.keyboard.press("Enter");
  const card = panel.locator(".list-card").filter({hasText: "Keyboard journey memory"});
  await expect(card).toBeVisible();
  operations.push("created memory");

  await tabTo(page, card.locator(".memory-edit-button"));
  await page.keyboard.press("Enter");
  await tabTo(page, panel.locator("#memory-content"));
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("Keyboard journey memory edited");
  await tabTo(page, panel.locator("#memory-save"));
  await page.keyboard.press("Enter");
  const edited = panel.locator(".list-card").filter({hasText: "Keyboard journey memory edited"});
  await expect(edited).toBeVisible();
  operations.push("edited memory");

  await tabTo(page, edited.locator(".delete-memory"));
  await page.keyboard.press("Enter");
  await tabTo(page, panel.locator("#confirm-accept"));
  await page.keyboard.press("Enter");
  await expect(edited).toHaveCount(0);
  await page.keyboard.press("Tab");
  await assertUsableFocus(page);
  operations.push("deleted memory");
  await expectHarnessClean(page, errors);
  await testInfo.attach("keyboard-operations", {body: JSON.stringify({layer: "fixture", operations}), contentType: "application/json"});
});

test("keyboard-only navigation, lazy routes and large-text viewport retain reachable focus", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  await page.setViewportSize({width: 390, height: 800});
  await page.goto(fixtureUrl("assistant/basics"));
  await page.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
  const panel = page.locator("extended-openai-management-panel");
  const top = panel.locator("#top-section-mobile");
  await tabTo(page, top);
  await page.keyboard.press("c");
  await page.keyboard.press("Enter");
  await expect(panel.locator("#local-section")).toBeVisible();
  const local = panel.locator("#local-section");
  await tabTo(page, local);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await assertUsableFocus(page);
  await page.keyboard.press("Shift+Tab");
  await assertUsableFocus(page);
  await testInfo.attach("keyboard-navigation", {body: JSON.stringify({layer: "fixture", viewport: 390, textScale: 2}), contentType: "application/json"});
  await expectHarnessClean(page, errors);
});

test("malformed browser-local state cannot prevent a fresh management load", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.addInitScript(() => {
    localStorage.setItem("extended-openai-agent", "stale-deleted-agent");
    sessionStorage.setItem("eocRealHaCalls", "not-json");
  });
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await page.goto(fixtureUrl("data-memory/memories"));
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expectHarnessClean(page, errors);
});


test("keyboard-only Request Rule journey creates, edits and deletes a routing rule", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  const operations = [];
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  const create = panel.getByRole("button", {name: "Create rule", exact: true}).first();

  await tabTo(page, create);
  await page.keyboard.press("Enter");
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#rule-name"));
  await page.keyboard.type("Keyboard routing rule");
  await tabTo(page, panel.locator("#rule-phrases"));
  await page.keyboard.type("keyboard routing phrase");
  const actionType = panel.locator("#rule-action-type");
  await tabTo(page, actionType);
  await page.keyboard.press("End");
  await expect(actionType).toHaveValue("model_routing");
  await tabTo(page, panel.locator("#rule-model"));
  await page.keyboard.type("gpt-5-mini");
  await tabTo(page, panel.locator("#rule-save"));
  await page.keyboard.press("Enter");

  let card = panel.locator(".request-rule-card").filter({hasText: "Keyboard routing rule"});
  await expect(card).toBeVisible();
  operations.push("created");

  await tabTo(page, card.locator(".rule-edit"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#rule-name"));
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("Keyboard routing rule edited");
  await tabTo(page, panel.locator("#rule-save"));
  await page.keyboard.press("Enter");
  card = panel.locator(".request-rule-card").filter({hasText: "Keyboard routing rule edited"});
  await expect(card).toBeVisible();
  operations.push("edited");

  await tabTo(page, card.locator(".rule-delete"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#confirm-accept"));
  await page.keyboard.press("Enter");
  await expect(card).toHaveCount(0);
  await tabTo(page, create);
  await assertUsableFocus(page);
  operations.push("deleted");

  await expectHarnessClean(page, errors);
  await testInfo.attach("keyboard-request-rule", {
    body: JSON.stringify({layer: "fixture", operations}),
    contentType: "application/json",
  });
});

test("keyboard-only Function Tool journey authors, edits and deletes YAML", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  const operations = [];
  await page.goto(fixtureUrl("capabilities/functions"));
  const panel = page.locator("extended-openai-management-panel");

  await tabTo(page, panel.locator("#function-add"));
  await page.keyboard.press("Enter");
  await tabTo(page, panel.locator("#add-tool"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  const yaml = panel.locator("#tool-yaml");
  await expect(yaml).toBeVisible();
  await tabTo(page, yaml);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText(browserToolYaml("Keyboard YAML tool"));
  await tabTo(page, panel.locator("#tool-save"));
  await page.keyboard.press("Enter");

  let card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Keyboard YAML tool");
  operations.push("created");

  await tabTo(page, card.locator(".edit-tool"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await expect(yaml).toBeVisible();
  await tabTo(page, yaml);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText(browserToolYaml("Keyboard YAML tool edited"));
  await tabTo(page, panel.locator("#tool-save"));
  await page.keyboard.press("Enter");
  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Keyboard YAML tool edited");
  operations.push("edited");

  await tabTo(page, card.locator(".delete-tool"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#confirm-accept"));
  await page.keyboard.press("Enter");
  await expect(card).toHaveCount(0);
  await tabTo(page, panel.locator("#function-add"));
  await assertUsableFocus(page);
  operations.push("deleted");

  await expectHarnessClean(page, errors);
  await testInfo.attach("keyboard-function-tool", {
    body: JSON.stringify({layer: "fixture", operations}),
    contentType: "application/json",
  });
});

test("keyboard-only Knowledge journey creates, edits and deletes a source", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  const operations = [];
  await page.goto(fixtureUrl("data-memory/knowledge"));
  const panel = page.locator("extended-openai-management-panel");

  await tabTo(page, panel.locator("#add-source"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#knowledge-title"));
  await page.keyboard.type("Keyboard Knowledge source");
  await tabTo(page, panel.locator("#knowledge-description"));
  await page.keyboard.type("Keyboard first revision");
  await tabTo(page, panel.locator("#knowledge-content"));
  await page.keyboard.type("Knowledge content authored without a pointer.");
  await tabTo(page, panel.locator("#knowledge-save"));
  await page.keyboard.press("Enter");

  let card = panel.locator(".list-card").filter({hasText: "Keyboard Knowledge source"});
  await expect(card).toBeVisible();
  operations.push("created");

  await tabTo(page, card.locator(".source-edit-button"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#knowledge-description"));
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("Keyboard second revision");
  await tabTo(page, panel.locator("#knowledge-save"));
  await page.keyboard.press("Enter");
  card = panel.locator(".list-card").filter({hasText: "Keyboard Knowledge source"});
  await expect(card).toContainText("Keyboard second revision");
  operations.push("edited");

  await tabTo(page, card.locator(".delete-source"));
  await page.keyboard.press("Enter");
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await tabTo(page, panel.locator("#confirm-accept"));
  await page.keyboard.press("Enter");
  await expect(card).toHaveCount(0);
  await tabTo(page, panel.locator("#add-source"));
  await assertUsableFocus(page);
  operations.push("deleted");

  await expectHarnessClean(page, errors);
  await testInfo.attach("keyboard-knowledge", {
    body: JSON.stringify({layer: "fixture", operations}),
    contentType: "application/json",
  });
});

test("keyboard-only Assistant configuration edit saves and survives reload", async ({page}, testInfo) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics"));
  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible();

  await tabTo(page, title);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("Keyboard saved assistant");
  const save = panel.locator("#save-config");
  await tabTo(page, save);
  await page.keyboard.press("Enter");
  await expect(panel.locator(".save-bar")).toHaveCount(0);

  // The harness replaces the URL with HA's route, which the static server
  // does not serve. Reload the fixture document for the same route instead.
  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Keyboard saved assistant");
  await assertUsableFocus(page, true);
  await expectHarnessClean(page, errors);
  await testInfo.attach("keyboard-assistant-save", {
    body: JSON.stringify({layer: "fixture", saved: true, reloaded: true}),
    contentType: "application/json",
  });
});
