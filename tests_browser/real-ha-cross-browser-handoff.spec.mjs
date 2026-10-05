import {chromium, firefox, webkit, expect, test} from "@playwright/test";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;
const middleName = process.env.REAL_HA_CROSS_BROWSER_MIDDLE;
const phase = process.env.REAL_HA_CROSS_BROWSER_PHASE || "handoff";

test.skip(!baseUrl || !authDataRaw || !middleName, "requires cross-browser genuine HA handoff harness");

const browserTypes = {chromium, firefox, webkit};
const toolYaml = (revision) => `spec:
  name: cross_engine_tool
  description: Cross-engine persisted tool
  parameters:
    type: object
    properties: {}
function:
  type: template
  value_template: cross-engine-${revision}
`;

async function freshEngine(name) {
  const browser = await browserTypes[name].launch();
  const context = await browser.newContext({locale: "en-IE", timezoneId: "Europe/Dublin"});
  const page = await context.newPage();
  return {browser, context, page};
}

async function openRoute(engine, route) {
  return await openColdHaRoute(engine.context, engine.page, route);
}

async function closeEngine(engine) {
  await engine.context.close();
  await engine.browser.close();
}

async function createRule(panel) {
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Cross-engine local rule");
  await panel.locator("#rule-phrases").fill("cross engine execute");
  await panel.locator("#rule-match").selectOption("equals");
  await panel.locator("#rule-action-type").selectOption("local_action");
  await panel.locator("#rule-action-sequence-host ha-selector").evaluate((selector) => {
    selector.value = [{delay:{milliseconds:1}}];
    selector.dispatchEvent(new CustomEvent("value-changed", {
      detail: {value: selector.value}, bubbles: true, composed: true,
    }));
  });
  await panel.locator("#rule-success").fill("Cross-engine rule complete");
  await panel.locator("#rule-failure").fill("Cross-engine rule failed");
  await panel.locator("#rule-save").click();
  await expect(panel.locator(".request-rule-card").filter({hasText:"Cross-engine local rule"})).toBeVisible();
}

async function executeRule(panel) {
  const live = panel.locator("#eoc-rule-live-test");
  await live.locator("summary").click();
  await panel.locator("#eoc-rule-live-text").fill("cross engine execute");
  await panel.locator("#eoc-rule-live-run").click();
  const confirm = panel.locator("#confirm-dialog");
  if (await confirm.waitFor({state:"visible", timeout:2000}).then(()=>true).catch(()=>false)) {
    await panel.locator("#confirm-accept").click();
  }
  await expect(panel.locator("#eoc-rule-live-result")).toContainText("Cross-engine rule complete");
}

async function assertPersistedCorpus(panel, middle) {
  await expect(panel.locator('[data-config="__title"]')).toHaveValue(`Cross-engine ${middle} edited`);
  await panel.evaluate(host => host._navigate("capabilities", "functions"));
  await expect(panel.locator('[data-tool-key="cross_engine_tool"]')).toContainText("Cross-engine persisted tool");
  await panel.locator('[data-tool-key="cross_engine_tool"] .edit-tool').click();
  await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(element => element.yaml)).toContain("cross-engine-v2");
  await panel.locator("#tool-dialog .dialog-actions .close-editor").click().catch(()=>{});
  await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
  await expect(panel.locator(".request-rule-card").filter({hasText:"Cross-engine local rule"})).toBeVisible();
  await panel.evaluate(host => host._navigate("data-memory", "memories"));
  await expect(panel.locator(".memory-list")).toContainText("Éire 東京 first writer");
  await expect(panel.locator(".memory-list")).toContainText(`${middle} second writer`);
}

test("browser engines hand persisted state across one HA backend", async () => {
  const auth = JSON.parse(authDataRaw);

  if (phase === "post-restart") {
    const finalEngine = await freshEngine("chromium");
    await finalEngine.context.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), auth);
    try {
      const panel = await openRoute(finalEngine, "assistant/basics");
      await assertPersistedCorpus(panel, middleName);
      await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
      await executeRule(panel);
    } finally {
      await closeEngine(finalEngine);
    }
    return;
  }

  const first = await freshEngine("chromium");
  await first.context.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), auth);
  try {
    let panel = await openRoute(first, "assistant/basics");
    await panel.locator('[data-config="__title"]').fill("Cross-engine Chromium initial");
    const maxTokens = panel.locator('[data-config="max_tokens"]');
    if (await maxTokens.count()) await maxTokens.fill("733");
    await panel.getByRole("button", {name:"Save changes", exact:true}).click();
    await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);

    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    await panel.locator("#function-add").click();
    await panel.locator("#add-tool").click();
    await replaceNativeYaml(first.page, panel.locator("#tool-yaml-native"), toolYaml("v1"));
    await panel.locator("#tool-validate").click();
    await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
    await panel.locator("#tool-save").click();
    await expect(panel.locator('[data-tool-key="cross_engine_tool"]')).toBeVisible();

    await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
    await createRule(panel);
    await executeRule(panel);

    await panel.evaluate(host => host._navigate("data-memory", "memories"));
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill("Éire 東京 first writer\nmultiline persisted payload");
    await panel.locator("#memory-category").fill("cross-engine");
    await panel.locator("#memory-save").click();
    const firstMemory = panel.locator(".memory-list .list-card").filter({hasText:"Éire 東京 first writer"});
    await expect(firstMemory).toBeVisible();
    await firstMemory.locator(".memory-edit-button").click();
    await panel.locator("#memory-valid-from").fill("2026-10-05T12:34:56+01:00");
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).not.toHaveJSProperty("open", true);
  } finally {
    await closeEngine(first);
  }

  const middle = await freshEngine(middleName);
  await middle.context.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), auth);
  try {
    let panel = await openRoute(middle, "assistant/basics");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Cross-engine Chromium initial");
    await panel.locator('[data-config="__title"]').fill(`Cross-engine ${middleName} edited`);
    await panel.getByRole("button", {name:"Save changes", exact:true}).click();
    await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);

    await panel.evaluate(host => host._navigate("capabilities", "functions"));
    const card = panel.locator('[data-tool-key="cross_engine_tool"]');
    await expect(card).toBeVisible();
    await card.locator(".edit-tool").click();
    await replaceNativeYaml(middle.page, panel.locator("#tool-yaml-native"), toolYaml("v2"));
    await panel.locator("#tool-validate").click();
    await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
    await panel.locator("#tool-save").click();

    await panel.evaluate(host => host._navigate("data-memory", "memories"));
    await expect(panel.locator(".memory-list")).toContainText("Éire 東京 first writer");
    const existing = panel.locator(".memory-list .list-card").filter({hasText:"Éire 東京 first writer"});
    await existing.locator(".memory-edit-button").click();
    await expect(panel.locator("#memory-valid-from")).toHaveValue("2026-10-05T12:34:56+01:00");
    await panel.locator("#memory-content").fill("Éire 東京 first writer edited by middle engine\nmultiline persisted payload");
    await panel.locator("#memory-save").click();

    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill(`${middleName} second writer`);
    await panel.locator("#memory-category").fill("cross-engine");
    await panel.locator("#memory-save").click();

    await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
    await executeRule(panel);
  } finally {
    await closeEngine(middle);
  }

  const returned = await freshEngine("chromium");
  await returned.context.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), auth);
  try {
    const panel = await openRoute(returned, "assistant/basics");
    await assertPersistedCorpus(panel, middleName);
    await panel.evaluate(host => host._navigate("capabilities", "request-rules"));
    await executeRule(panel);
  } finally {
    await closeEngine(returned);
  }
});
