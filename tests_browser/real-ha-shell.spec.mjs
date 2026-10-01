import {expect, test} from "@playwright/test";
import {managementRouteState, waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;

test.skip(!baseUrl || !authDataRaw, "requires the dedicated genuine Home Assistant frontend-shell harness");

async function authenticate(context) {
  const authData = JSON.parse(authDataRaw);
  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);
}

async function openAssistantFromOverview(page) {
  const confirmHttpSettings = page.getByRole("button", {name: "Confirm", exact: true});

  // Keep this acceptance seam deliberately narrow: enter the registered HA panel
  // root first, let Home Assistant instantiate the custom panel, then use the
  // integration's own navigation to reach Assistant/Basics.
  await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);

  const confirmationVisible = await confirmHttpSettings
    .waitFor({state: "visible", timeout: 2_000})
    .then(() => true)
    .catch(() => false);
  if (confirmationVisible) {
    await confirmHttpSettings.click();
    await expect(confirmHttpSettings).toHaveCount(0);
    await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
    await expect(page.locator("home-assistant")).toHaveCount(1);
  }

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await expect(panel.getByRole("heading", {name: "Extended OpenAI", exact: true})).toBeVisible({timeout: 30_000});

  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible({timeout: 30_000});
  return panel;
}

async function openFunctionsFromOverview(page) {
  const panel = await openAssistantFromOverview(page);
  await panel.getByRole("button", {name: "Capabilities", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/home-assistant$/);
  await panel.getByRole("button", {name: "Functions", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/capabilities\/functions$/);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  return panel;
}

test("cold native Guest and Request Rule editors receive HA context and discard cancelled YAML", async ({context,page}) => {
  await authenticate(context);
  const panel=await openAssistantFromOverview(page);
  // The pristine HA fixture emits unrelated startup websocket rejections.
  // Capture every error throughout the native interactions under test.
  const errors=[];
  page.on("pageerror",error=>errors.push(error.message));
  await panel.evaluate(host=>host._navigate("capabilities","guest-mode"));
  await expect(panel.locator(".guest-intro")).toBeVisible();
  if (await panel.locator("#guest-review-converted").count()) await panel.locator("#guest-review-converted").click();
  expect(await panel.evaluate(host=>host.hass===host._hass)).toBe(true);
  const guestSelector=panel.locator("ha-selector[data-guest-key]").first();
  expect(await guestSelector.evaluate(selector=>Boolean(selector.hass?.locale&&selector.hass?.localize))).toBe(true);
  await panel.evaluate(host=>host._navigate("capabilities","request-rules"));
  await expect(panel.locator("#rule-add")).toBeVisible();
  const name="Cold native cancelled YAML regression";
  await panel.evaluate(async(host,name)=>{
    try {
      await host._call("request_rules","create",{revision:host._result.revision,rule:{name,phrases:["cold native test"],match_type:"equals",action_type:"model_routing",action:{model:"gpt-5-mini",scope:"request",continue_to_ai:true,success_response:"Updated"},conditions:[{condition:"template",value_template:"{{ false }}"}]}});
    } catch (error) { throw new Error(error.message || JSON.stringify(error)); }
    await host._loadSection(true);
  },name);
  const card=panel.locator(".request-rule-card").filter({hasText:name});
  await card.locator(".rule-edit").click();
  const condition=panel.locator("#rule-condition-host > ha-selector");
  const row=condition.locator("ha-automation-condition-row").first();
  await expect(row).toBeVisible();
  await expect.poll(()=>condition.evaluate(selector=>selector.hass.localize("ui.panel.config.automation.editor.conditions.add"))).toBe("Add condition");
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  const yaml=condition.locator("ha-yaml-editor");
  await expect(yaml).toBeVisible();
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("false");
  await yaml.locator(".cm-content").fill("condition: template\nvalue_template: '{{ true }}'");
  await expect.poll(()=>condition.evaluate(selector=>selector.value[0]?.value_template)).toBe("{{ true }}");
  await panel.locator("#rule-dialog .rule-close").filter({hasText:"Cancel"}).click();
  await card.locator(".rule-edit").click();
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("false");
  await yaml.locator(".cm-content").fill("condition: template\nvalue_template: '{{ true }}'");
  await expect.poll(()=>condition.evaluate(selector=>selector.value[0]?.value_template)).toBe("{{ true }}");
  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open",false);
  await card.locator(".rule-edit").click();
  await row.evaluate(element=>{element._yamlMode=true;element.requestUpdate();});
  await row.locator("ha-expansion-panel").evaluate(element=>{element.expanded=true;});
  await expect.poll(()=>yaml.evaluate(element=>element.yaml)).toContain("true");
  await panel.locator("#rule-dialog .rule-close").filter({hasText:"Cancel"}).click();
  expect(errors).toEqual([]);
});

test("latency readiness helper sees panel inside genuine HA shadow DOM", async ({context, page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/overview`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  await expect(page.locator("extended-openai-management-panel")).toHaveCount(1);

  const route = {name: "overview", path: "overview"};
  await waitForManagementRouteReady(page, route, 30_000);
  const state = await managementRouteState(page);
  expect(state).toMatchObject({
    page: "overview",
    subsection: null,
    busy: false,
    error: null,
    loading: false,
  });
  expect(String(state?.renderedRoute || "")).toContain("|overview");
});

test("shipped management panel loads and persists one configuration change inside the genuine HA frontend", async ({context, page}) => {
  const integrationPageErrors = [];
  const integrationConsoleErrors = [];
  const integrationRequestFailures = [];
  const integrationResponses = [];

  page.on("pageerror", (error) => {
    const detail = [error.name, error.message, error.stack].filter(Boolean).join("\n");
    if (detail.includes("/extended_openai_conversation_responses/")) {
      integrationPageErrors.push(detail);
    }
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const location = message.location();
    const detail = `${message.text()}${location?.url ? ` (${location.url}:${location.lineNumber ?? 0})` : ""}`;
    if (
      location?.url?.includes("/extended_openai_conversation_responses/")
      || detail.includes("extended_openai_conversation_responses")
      || detail.includes("extended-openai-management-panel")
    ) {
      integrationConsoleErrors.push(detail);
    }
  });
  page.on("requestfailed", (request) => {
    if (request.url().includes("/extended_openai_conversation_responses/")) {
      integrationRequestFailures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "request failed"}`);
    }
  });
  page.on("response", (response) => {
    if (response.url().includes("/extended_openai_conversation_responses/")) {
      integrationResponses.push({url: response.url(), status: response.status()});
    }
  });

  await authenticate(context);

  // Genuine HA owns authentication, routing, custom-panel registration, and the
  // websocket. This test proves only that integration boundary plus one real save.
  let panel = await openAssistantFromOverview(page);
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();

  const title = panel.locator('[data-config="__title"]');
  await title.fill("Real HA shell saved");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  // Re-enter through HA's registered panel root instead of relying on a deep-route
  // reload. Persistence is still proved through a fresh panel lifecycle.
  panel = await openAssistantFromOverview(page);
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Real HA shell saved");
  await expect(panel.locator("#agent option:checked")).toHaveText("Real HA shell saved");

  expect(integrationRequestFailures).toEqual([]);
  expect(integrationPageErrors).toEqual([]);
  expect(integrationConsoleErrors).toEqual([]);
  expect(integrationResponses.some(({url, status}) => {
    const path = new URL(url).pathname;
    return /^\/extended_openai_conversation_responses\/frontend\/assets\/management-[A-Za-z0-9_-]+\.js$/.test(path)
      && status === 200;
  })).toBe(true);
  expect(integrationResponses.filter(({status}) => status >= 400)).toEqual([]);
});

test("genuine Home Assistant shell follows deep links and browser history", async ({context, page}) => {
  const authData = JSON.parse(authDataRaw);
  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);

  // Start on a nested deep link, not the panel's normal landing page.
  await page.goto(`${baseUrl}/extended-openai/data-memory/knowledge`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/data-memory/knowledge`);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  // Build history through the shipped panel's own navigation handlers.
  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await panel.getByRole("button", {name: "Capabilities", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await panel.getByRole("button", {name: "Functions", exact: true}).click();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/functions`);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();

  // Home Assistant owns popstate handling. The custom panel must follow the URL
  // backward and forward instead of leaving stale content from the previous view.
  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await page.goBack();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/data-memory/knowledge`);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/assistant/basics`);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/home-assistant`);
  await expect(panel.getByText("Use Extended OpenAI local handling", {exact: true})).toBeVisible();

  await page.goForward();
  await expect(page).toHaveURL(`${baseUrl}/extended-openai/capabilities/functions`);
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
});

test("genuine HA native YAML editor saves with Ctrl+S and survives a fresh panel lifecycle", async ({context, page}) => {
  await authenticate(context);
  let panel = await openFunctionsFromOverview(page);

  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  const nativeEditor = panel.locator("#tool-yaml-native");
  const fallback = panel.locator("#tool-yaml");
  await expect(nativeEditor).toBeVisible({timeout: 30_000});
  await expect(fallback).toBeHidden();
  await expect(nativeEditor).toHaveJSProperty("tagName", "HA-YAML-EDITOR");
  expect(await nativeEditor.evaluate(() => Boolean(customElements.get("ha-yaml-editor")))).toBe(true);

  const initialTool = {
    spec: {
      name: "real_shell_native_tool",
      description: "Genuine HA native YAML editor",
      parameters: {type: "object", properties: {}},
    },
    function: {type: "native", name: "get_user_from_user_id"},
  };
  await nativeEditor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, initialTool);
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");

  // Exercise keyboard reachability through the real HA component before using its
  // native save shortcut. Hidden fallback controls must not trap focus.
  await panel.locator("#built-in-function").focus();
  await page.keyboard.press("Tab");
  await expect.poll(() => nativeEditor.evaluate((element) => element.matches(":focus-within"))).toBe(true);
  expect(await nativeEditor.evaluate((element) => Boolean(element.shadowRoot?.activeElement))).toBe(true);
  await page.keyboard.press("Control+s");

  let card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor");
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"})).toHaveCount(1);

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor");
  await card.locator(".edit-tool").click();
  const reopenedEditor = panel.locator("#tool-yaml-native");
  await expect(reopenedEditor).toBeVisible();
  await expect.poll(() => reopenedEditor.evaluate((element) => element.yaml)).toContain("real_shell_native_tool");

  const editedTool = structuredClone(initialTool);
  editedTool.spec.description = "Genuine HA native YAML editor edited";
  await reopenedEditor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, editedTool);
  await panel.locator("#tool-validate").click();
  await expect(panel.locator("#tool-error")).toHaveClass(/valid/);
  await expect(panel.locator("#tool-error")).toContainText("Name: real_shell_native_tool");
  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  await expect(card).toContainText("Genuine HA native YAML editor edited");

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await expect(card).toContainText("Genuine HA native YAML editor edited");

  // Prove the panel remains healthy outside Function Tools after native-editor use.
  await panel.getByRole("button", {name: "Data & Memory", exact: true}).click();
  await panel.getByRole("button", {name: "Knowledge Library", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/data-memory\/knowledge$/);
  await expect(panel.getByRole("heading", {name: "Sources", exact: true})).toBeVisible();

  // Clean up the acceptance tool so this test remains friendly to retries.
  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"});
  await card.locator(".delete-tool").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_native_tool"})).toHaveCount(0);
});

test("long native Function YAML scrolls inside the editor while dialog actions stay visible", async ({context, page}) => {
  await authenticate(context);
  const panel = await openFunctionsFromOverview(page);
  await panel.locator("#add-tool").click();
  const dialog = panel.locator("#tool-dialog");
  const editor = panel.locator("#tool-yaml-native");
  await expect(editor).toBeVisible({timeout: 30_000});

  const properties = Object.fromEntries(Array.from({length: 90}, (_, index) => [
    `long_field_${String(index).padStart(3, "0")}`,
    {type: "string", description: `A long YAML editor scroll fixture field ${index}`},
  ]));
  const longTool = {
    spec: {
      name: "real_shell_long_yaml_scroll_tool",
      description: "Long YAML editor scroll fixture",
      parameters: {type: "object", properties},
    },
    function: {type: "native", name: "get_user_from_user_id"},
  };
  await editor.evaluate((element, value) => {
    element.setValue(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      bubbles: true,
      composed: true,
      detail: {value, isValid: true, errorMsg: ""},
    }));
  }, longTool);
  await expect.poll(() => editor.evaluate((element) => element.yaml.split("\n").length)).toBeGreaterThan(200);

  const actions = dialog.locator(".dialog-actions");
  const initialLayout = await dialog.evaluate((element) => {
    const rect = (node) => {
      const {top, bottom} = node.getBoundingClientRect();
      return {top, bottom};
    };
    return {dialog: rect(element), actions: rect(element.querySelector(".dialog-actions"))};
  });
  expect(initialLayout.dialog.top).toBeGreaterThanOrEqual(0);
  expect(initialLayout.dialog.bottom).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  expect(initialLayout.actions.bottom).toBeLessThanOrEqual(initialLayout.dialog.bottom);
  await expect(actions.getByRole("button", {name: "Save", exact: true})).toBeVisible();

  const scrollState = () => editor.evaluate((host) => {
    const scrollables = [];
    const visit = (root) => {
      for (const element of root.querySelectorAll("*")) {
        const style = getComputedStyle(element);
        if (element.scrollHeight > element.clientHeight + 2 && /auto|scroll/.test(style.overflowY)) {
          scrollables.push(element);
        }
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    if (host.scrollHeight > host.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(host).overflowY)) {
      scrollables.push(host);
    }
    if (host.shadowRoot) visit(host.shadowRoot);
    return {
      count: scrollables.length,
      top: Math.max(0, ...scrollables.map((element) => element.scrollTop)),
    };
  });
  await expect.poll(async () => (await scrollState()).count).toBeGreaterThan(0);
  const editorRect = await editor.boundingBox();
  expect(editorRect).not.toBeNull();
  await page.mouse.move(editorRect.x + editorRect.width / 2, editorRect.y + editorRect.height / 2);
  await page.mouse.wheel(0, 700);
  await page.waitForTimeout(300);
  await expect.poll(async () => (await scrollState()).top).toBeGreaterThan(0);

  const finalActions = await actions.boundingBox();
  expect(finalActions).not.toBeNull();
  expect(finalActions.y).toBeCloseTo(initialLayout.actions.top, 0);
  expect(finalActions.y + finalActions.height).toBeLessThanOrEqual(await page.evaluate(() => window.innerHeight));
  await expect(actions.getByRole("button", {name: "Save", exact: true})).toBeVisible();
  await panel.locator("#tool-cancel").click();
  await expect(dialog).toHaveJSProperty("open", false);
});

test("genuine HA YAML keyboard edits validate before one persisted save", async ({context, page}) => {
  await authenticate(context);
  let panel = await openFunctionsFromOverview(page);
  await panel.evaluate((element) => {
    window.__keyboardToolSaves = [];
    const original = element._hass.callWS.bind(element._hass);
    element._hass.callWS = async (message) => {
      if (message.section === "tools" && message.action === "save") {
        window.__keyboardToolSaves.push(structuredClone(message));
      }
      return original(message);
    };
  });
  await panel.locator("#add-tool").click();
  const dialog = panel.locator("#tool-dialog");
  const editor = panel.locator("#tool-yaml-native");
  await expect(editor).toBeVisible({timeout: 30_000});
  const surface = editor.locator('[contenteditable="true"], textarea').first();
  await expect(surface).toBeVisible();

  const yaml = (description) => `spec:\n  name: real_shell_keyboard_tool\n  description: ${description}\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: get_user_from_user_id\n`;
  const replaceThroughKeyboard = async (value) => {
    await surface.click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(value);
    await expect.poll(() => editor.evaluate((element) => element.yaml)).toBe(value);
  };

  await replaceThroughKeyboard(yaml("First valid keyboard edit"));
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");
  await replaceThroughKeyboard(yaml("[unterminated"));
  await expect(panel.locator("#tool-error")).toHaveClass(/invalid/);
  await panel.locator("#tool-save").click();
  await expect(dialog).toHaveJSProperty("open", true);
  await expect(panel.locator("#tool-error")).toHaveClass(/invalid/);
  await expect(panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"})).toHaveCount(0);
  expect(await page.evaluate(() => window.__keyboardToolSaves)).toHaveLength(0);

  await replaceThroughKeyboard(yaml("Final corrected keyboard edit"));
  await expect(panel.locator("#tool-error")).toContainText("YAML changed");
  await panel.locator("#tool-save").click();
  await expect(dialog).toHaveJSProperty("open", false);
  let card = panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"});
  await expect(card).toContainText("Final corrected keyboard edit");
  await expect(card).not.toContainText("First valid keyboard edit");
  expect(await page.evaluate(() => window.__keyboardToolSaves)).toHaveLength(1);

  panel = await openFunctionsFromOverview(page);
  card = panel.locator(".tool-card").filter({hasText: "real_shell_keyboard_tool"});
  await expect(card).toContainText("Final corrected keyboard edit");
  await card.locator(".delete-tool").click();
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#confirm-accept").click();
  await expect(card).toHaveCount(0);
});

test("cold Voice user pickers select a secondary HA user and keep its name after save/reload", async ({context,page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/assistant/voice`, {waitUntil:"domcontentloaded"});
  const panel=page.locator("extended-openai-management-panel");
  const defaultPicker=panel.locator('#config-voice_default_user_picker');
  await expect(defaultPicker.locator('ha-generic-picker')).toHaveCount(1);
  const secondaryName="Cold Voice secondary user";
  const secondaryId=await panel.evaluate(async(host,name)=>{
    let users=await host.hass.callWS({type:"config/auth/list"});
    if(!users.some(user=>user.name===name)) {
      await host.hass.callWS({type:"config/auth/create",name});
      users=await host.hass.callWS({type:"config/auth/list"});
    }
    const user=users.find(user=>user.name===name);
    // Seed an unavailable device to exercise saved ownership without hardware.
    host._draft.voice_default_user_id="";
    host._draft.voice_device_mappings={"cold-voice-regression-device":"unretained"};
    host._configDirty=true;
    host._render();
    return user.id;
  },secondaryName);
  await panel.locator('[data-config="voice_scope_policy"]').selectOption('device_mapping');
  await panel.locator('[data-config="voice_unmapped_policy"]').selectOption('default_user');
  // The user was created after the initial catalogue fetch; a fresh cold reload
  // verifies native registration and obtains the current HA users.
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await expect(defaultPicker.locator('ha-generic-picker')).toHaveCount(1);
  await defaultPicker.locator('ha-picker-field').click();
  await defaultPicker.locator('ha-picker-combo-box ha-combo-box-item').filter({hasText:secondaryName}).locator('button').click();
  await expect(defaultPicker).toHaveJSProperty('value',secondaryId);
  const mappingPicker=panel.locator('.voice-owner-user-picker');
  await panel.locator('.voice-owner-type').selectOption('user');
  await expect(mappingPicker.locator('ha-generic-picker')).toHaveCount(1);
  await mappingPicker.locator('ha-picker-field').click();
  await mappingPicker.locator('ha-picker-combo-box ha-combo-box-item').filter({hasText:secondaryName}).locator('button').click();
  await expect(mappingPicker).toHaveJSProperty('value',secondaryId);
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await expect(defaultPicker).toHaveJSProperty('value',secondaryId);
  await expect(mappingPicker).toHaveJSProperty('value',secondaryId);
  await expect(defaultPicker).toContainText(secondaryName);
  await expect(mappingPicker).toContainText(secondaryName);
  await expect(panel.locator('#voice-current-summary')).toContainText(secondaryName);
  await expect(defaultPicker).not.toContainText('Unknown user selected');
  await expect(mappingPicker).not.toContainText('Unknown user selected');
  const saved=await panel.evaluate(host=>host._draft.voice_device_mappings);
  expect(saved['cold-voice-regression-device']).toBe(`user:${secondaryId}`);
});

test("cold Prompt entity editor switches immediately and saves through the native HA shell", async ({context,page}) => {
  await authenticate(context);
  await page.goto(`${baseUrl}/extended-openai/assistant/prompt-context`,{waitUntil:"domcontentloaded"});
  const panel=page.locator('extended-openai-management-panel');
  await panel.locator('#exposed-entity-picker').waitFor({state:"attached"});
  await panel.locator('[data-config="exposed_entities_enabled"]').check();
  const select=async entityId=>{
    const picker=panel.locator('#exposed-entity-picker');
    await picker.evaluate((element,value)=>{element.value=value;element.dispatchEvent(new CustomEvent('value-changed',{detail:{value},bubbles:true}));},entityId);
  };
  await select('sensor.cold_attribute_kitchen');
  await expect(panel.locator('[data-exposed-editor]')).toContainText('sensor.cold_attribute_kitchen');
  await select('sensor.cold_attribute_hall');
  await expect(panel.locator('[data-exposed-editor]')).toContainText('sensor.cold_attribute_hall');
  await panel.locator('[data-exposed-attribute][data-attribute="battery_level"]').check();
  await panel.locator('[data-close-exposed-editor]').click();
  await expect(panel.locator('[data-exposed-editor]')).toHaveCount(0);
  await panel.locator('[data-edit-exposed-entity="sensor.cold_attribute_hall"]').click();
  await expect(panel.locator('[data-exposed-attribute][data-attribute="battery_level"]')).toBeChecked();
  await panel.locator('#save-config').click();
  await expect.poll(()=>panel.evaluate(host=>host._configDirty)).toBe(false);
  await page.reload();
  await panel.locator('[data-edit-exposed-entity="sensor.cold_attribute_hall"]').click();
  await expect(panel.locator('[data-exposed-attribute][data-attribute="battery_level"]')).toBeChecked();
});
