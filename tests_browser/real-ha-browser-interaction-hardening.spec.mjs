import {expect, test} from "@playwright/test";
import {readFile} from "node:fs/promises";
import {openColdHaRoute, replaceNativeYaml} from "./real-ha-shell-helpers.mjs";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;

test.skip(!baseUrl || !authDataRaw, "requires the genuine Home Assistant browser harness");

async function openRoute(context, page, route) {
  return openColdHaRoute(context, page, route);
}

async function createSimpleRule(panel, name, phrase) {
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await panel.locator("#rule-name").fill(name);
  await panel.locator("#rule-phrases").fill(phrase);
  await panel.locator("#rule-match").selectOption("equals");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-continue-to-ai").uncheck();
  await panel.locator("#rule-routing-success").fill("Cross-agent pack executed");
  await panel.locator("#rule-save").click();
  await expect(panel.locator(".request-rule-card").filter({hasText:name})).toBeVisible();
}

test("stale Memory editor cannot resurrect state restored by another real HA tab", async ({context, page}) => {
  test.setTimeout(120000);
  const other = await context.newPage();
  const marker = "Browser restore authority marker";
  const preRestore = "Browser pre-restore marker";
  const staleDraft = "Browser stale draft must lose";
  try {
    const panelA = await openRoute(context, page, "data-memory/memories");
    await panelA.locator("#add-memory").click();
    await panelA.locator("#memory-content").fill(marker);
    await panelA.locator("#memory-category").fill("browser-hardening");
    await panelA.locator("#memory-save").click();
    await expect(panelA.getByText(marker, {exact:true})).toBeVisible();

    const backup = await panelA.evaluate(async host => host._call("backup", "create"));
    expect(backup.json).toBeTruthy();

    let card = panelA.locator(".list-card").filter({hasText:marker});
    await card.locator(".memory-edit-button").click();
    await panelA.locator("#memory-content").fill(preRestore);
    await panelA.locator("#memory-save").click();
    await expect(panelA.getByText(preRestore, {exact:true})).toBeVisible();

    const panelB = await openRoute(context, other, "data-memory/memories");
    card = panelA.locator(".list-card").filter({hasText:preRestore});
    await card.locator(".memory-edit-button").click();
    await panelA.locator("#memory-content").fill(staleDraft);

    await panelB.evaluate(async (host, json) => {
      const document = JSON.parse(json);
      await host._call("backup", "restore", {document, confirm:true});
    }, backup.json);

    await panelA.locator("#memory-save").click();
    await expect(panelA.locator("#memory-error")).toContainText(/changed|stale|restore/i);
    await expect(panelA.locator("#memory-content")).toHaveValue(staleDraft);

    await page.reload();
    await expect(panelA.getByText(marker, {exact:true})).toBeVisible();
    await expect(panelA.getByText(staleDraft, {exact:true})).toHaveCount(0);
    await expect(panelA.getByText(preRestore, {exact:true})).toHaveCount(0);
  } finally {
    await other.close();
  }
});

test("Rule Pack download from one assistant imports, conflicts, enables and executes on another", async ({context, page}) => {
  test.setTimeout(120000);
  const panel = await openRoute(context, page, "capabilities/request-rules");
  const ruleName = "Cross-agent file-pack rule";
  const phrase = "cross agent file pack phrase";
  await createSimpleRule(panel, ruleName, phrase);

  const sharing = panel.locator("#rule-sharing");
  if (!(await sharing.evaluate(element => element.open))) await sharing.locator("summary").click();
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#rule-pack-export").click();
  const download = await downloadPromise;
  const packPath = await download.path();
  expect(packPath).toBeTruthy();
  const pack = JSON.parse(await readFile(packPath, "utf8"));
  expect(pack.rules.some(rule => rule.name === ruleName)).toBe(true);

  const duplicate = await panel.evaluate(host =>
    host._call("configuration", "duplicate", {title:"Cross-agent Rule Pack destination"})
  );
  expect(duplicate.subentry_id).toBeTruthy();
  await panel.locator("#agent").selectOption(duplicate.subentry_id);
  await expect(panel.locator("#agent option:checked")).toContainText("Cross-agent Rule Pack destination");

  // Seed a same-name destination rule so the import exercises the conflict path
  // rather than only the empty-agent happy path.
  await createSimpleRule(panel, ruleName, phrase + " destination existing");

  if (!(await sharing.evaluate(element => element.open))) await sharing.locator("summary").click();
  await panel.locator("#rule-pack-file").setInputFiles(packPath);
  await panel.locator("#rule-pack-review-button").click();
  await expect(panel.locator(".rule-pack-review")).toContainText(ruleName);
  await panel.locator("#rule-pack-confirm").click();
  await expect(panel.locator("#rule-pack-message")).toContainText(/imported/i);

  const imported = panel.locator(".request-rule-card").filter({hasText:ruleName});
  expect(await imported.count()).toBeGreaterThanOrEqual(2);
  const disabled = imported.filter({has:panel.locator(".rule-enabled:not(:checked)")}).last();
  if (await disabled.count()) {
    await disabled.locator(".rule-enabled").check();
  } else {
    const candidates = imported.locator(".rule-enabled");
    const count = await candidates.count();
    for (let index = 0; index < count; index++) {
      if (!(await candidates.nth(index).isChecked())) {
        await candidates.nth(index).check();
        break;
      }
    }
  }

  const executed = await panel.evaluate((host, text) =>
    host._call("request_rules", "test", {text}), phrase);
  expect(executed.response).toContain("Cross-agent pack executed");
  expect(executed.handled_locally).toBe(true);
});

test("nested Request Rule conditions can be authored through visible HA controls only", async ({context, page}) => {
  test.setTimeout(120000);
  const panel = await openRoute(context, page, "capabilities/request-rules");
  await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await panel.locator("#rule-name").fill("Visible nested condition rule");
  await panel.locator("#rule-phrases").fill("visible nested condition phrase");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-continue-to-ai").uncheck();
  await panel.locator("#rule-routing-success").fill("Visible nested condition matched");
  await panel.locator("#rule-add-conditions").click();

  const selector = panel.locator("#rule-condition-host > ha-selector");
  await expect(selector).toBeVisible();
  const add = selector.getByRole("button", {name:/add condition/i}).first();
  await expect(add).toBeVisible();
  await add.click();

  // Home Assistant owns these selector menus. Use their rendered controls and
  // accessible labels rather than assigning selector.value or editing YAML.
  const andChoice = page.getByRole("menuitem", {name:/and/i}).first();
  await expect(andChoice).toBeVisible();
  await andChoice.click();

  const rootRow = selector.locator("ha-automation-condition-row").first();
  await expect(rootRow).toBeVisible();
  const childAdd = rootRow.getByRole("button", {name:/add condition/i}).first();
  for (const expression of ["{{ true }}", "{{ is_state('sun.sun', 'above_horizon') }}"]) {
    await childAdd.click();
    const templateChoice = page.getByRole("menuitem", {name:/template/i}).first();
    await expect(templateChoice).toBeVisible();
    await templateChoice.click();
    const fields = rootRow.locator("textarea, input");
    const target = fields.last();
    await expect(target).toBeVisible();
    await target.fill(expression);
  }

  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  const snapshot = await panel.evaluate(host => host._call("request_rules", "list"));
  const rule = snapshot.rules.find(item => item.name === "Visible nested condition rule");
  expect(rule.conditions).toHaveLength(1);
  expect(rule.conditions[0].condition).toBe("and");
  expect(rule.conditions[0].conditions).toHaveLength(2);
  expect(rule.conditions[0].conditions.every(item => item.condition === "template")).toBe(true);
});

test("RTL document, mixed-direction text and locale decimal input survive genuine HA saves", async ({browser}) => {
  test.setTimeout(120000);
  const context = await browser.newContext({locale:"ar-EG"});
  await context.addInitScript(() => {
    document.documentElement.lang = "ar";
    document.documentElement.dir = "rtl";
  });
  const page = await context.newPage();
  try {
    const panel = await openRoute(context, page, "assistant/prompt-context");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    const prompt = panel.locator("#prompt-editor");
    const mixed = "مرحبا light.kitchen — sensor.درجة_الحرارة — اختبار 123";
    await prompt.fill(mixed);
    await panel.locator("#save-config").click();
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await page.reload();
    await expect(prompt).toHaveValue(mixed);

    await panel.evaluate(host => host._navigate("assistant", "model-responses"));
    const decimal = panel.locator('input[type="number"][step]:not([step="1"])').first();
    await expect(decimal).toBeVisible();
    await decimal.focus();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.type("0,7");
    const typed = await decimal.inputValue();
    const key = await decimal.getAttribute("data-config");
    if (typed === "0,7" || typed === "0.7") {
      await panel.locator("#save-config").click();
      await expect(panel.locator(".save-bar")).toHaveCount(0);
      const saved = await panel.evaluate(host => host._call("configuration", "get"));
      if (key) expect(Number(saved.config[key])).toBeCloseTo(0.7);
    } else {
      // Native number controls may reject a locale decimal separator. That is
      // acceptable only if EOAI leaves the setting dirty/invalid rather than
      // silently saving a different numeric value.
      await expect(panel.locator("#save-config")).toBeEnabled();
      const before = await panel.evaluate(host => host._call("configuration", "get"));
      await panel.locator("#save-config").click();
      const after = await panel.evaluate(host => host._call("configuration", "get"));
      if (key) expect(after.config[key]).toBe(before.config[key]);
    }
  } finally {
    await context.close();
  }
});

test("IME composition preserves prompt, Knowledge, Memory, YAML comments and search input", async ({context, page}) => {
  test.setTimeout(120000);
  const compose = async (locator, value) => {
    await locator.dispatchEvent("compositionstart", {data:""});
    await locator.fill(value);
    await locator.dispatchEvent("keydown", {key:"Enter", code:"Enter", isComposing:true, keyCode:229});
    await expect(locator).toHaveValue(value);
    await locator.dispatchEvent("compositionend", {data:value});
    await expect(locator).toHaveValue(value);
  };

  let panel = await openRoute(context, page, "assistant/prompt-context");
  await compose(panel.locator("#prompt-editor"), "日本語プロンプト編集中");
  await panel.locator("#save-config").click();
  await expect(panel.locator(".save-bar")).toHaveCount(0);

  await panel.evaluate(host => host._navigate("data-memory", "knowledge"));
  await panel.locator("#add-source").click();
  await compose(panel.locator("#knowledge-title"), "知識タイトル");
  await compose(panel.locator("#knowledge-content"), "知識本文を変換中");
  await expect(panel.locator("#knowledge-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#knowledge-save").click();
  await expect(panel.getByText("知識タイトル", {exact:true})).toBeVisible();

  await panel.evaluate(host => host._navigate("data-memory", "memories"));
  await panel.locator("#add-memory").click();
  await compose(panel.locator("#memory-content"), "記憶の入力候補");
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("記憶の入力候補", {exact:true})).toBeVisible();
  await compose(panel.locator("#list-search"), "記憶");
  await expect(panel.locator("#list-search")).toHaveValue("記憶");

  await panel.evaluate(host => host._navigate("capabilities", "functions"));
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const yaml = panel.locator("#tool-yaml-native");
  await replaceNativeYaml(page, yaml, "spec:\n  name: ime_yaml_probe\n  description: IME YAML\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: template\n  value_template: ok\n# 日本語コメント\n");
  const surface = yaml.locator('[contenteditable="true"], textarea').first();
  await surface.dispatchEvent("compositionstart", {data:""});
  await surface.dispatchEvent("compositionend", {data:"日本語コメント"});
  await expect.poll(() => yaml.evaluate(element => element.yaml)).toContain("# 日本語コメント");
  await panel.locator("#tool-save").click();
  await expect(panel.locator('[data-tool-key="ime_yaml_probe"]')).toBeVisible();
});

test("real clipboard round-trip preserves multiline native YAML semantics", async ({context, page}) => {
  test.setTimeout(120000);
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {origin:baseUrl});
  const panel = await openRoute(context, page, "capabilities/functions");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const editor = panel.locator("#tool-yaml-native");
  await expect(editor).toBeVisible({timeout:30000});
  await expect.poll(() => editor.evaluate(element => element.yaml?.length || 0)).toBeGreaterThan(0);

  const document = "spec:\r\n  name: clipboard_yaml_probe\r\n  description: \"Unicode Δ 東京\\tTabbed\"\r\n  parameters:\r\n    type: object\r\n    properties: {}\r\nfunction:\r\n  type: template\r\n  value_template: \"line one\\nline two\"\r\n# no final newline";
  await page.evaluate(value => navigator.clipboard.writeText(value), document);
  const surface = editor.locator('[contenteditable="true"], textarea').first();
  await surface.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("ControlOrMeta+V");
  await expect.poll(() => editor.evaluate(element => element.yaml)).toContain("clipboard_yaml_probe");
  await expect.poll(() => editor.evaluate(element => element.yaml)).toContain("Unicode Δ 東京");

  // Copy back from the real CodeMirror surface before saving. Newline
  // normalization is allowed; semantic text and the missing trailing newline are not.
  await surface.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("ControlOrMeta+C");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied.replace(/\r\n/g, "\n")).toContain("clipboard_yaml_probe");
  expect(copied).toContain("Unicode Δ 東京");
  expect(copied.endsWith("\n")).toBe(false);

  await panel.locator("#tool-save").click();
  await expect(panel.locator('[data-tool-key="clipboard_yaml_probe"]')).toBeVisible();
  await panel.locator('[data-tool-key="clipboard_yaml_probe"] .edit-tool').click();
  await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(element => element.yaml)).toContain("line one");
  await expect.poll(() => panel.locator("#tool-yaml-native").evaluate(element => element.yaml)).toContain("line two");
});
