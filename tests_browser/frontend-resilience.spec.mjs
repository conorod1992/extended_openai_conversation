import {expect, test} from "@playwright/test";
import {acceptConfirmation, browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function failNextManagementCall(page, section, action, message) {
  await page.evaluate(({section, action, message}) => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let failed = false;
    hass.callWS = async (request) => {
      if (!failed && request.section === section && request.action === action) {
        failed = true;
        throw new Error(message);
      }
      return original(request);
    };
  }, {section, action, message});
}

async function returnNextManagementResult(page, section, action, result) {
  await page.evaluate(({section, action, result}) => {
    const hass = window.browserHarness.hass;
    const original = hass.callWS.bind(hass);
    let returned = false;
    hass.callWS = async (request) => {
      if (!returned && request.section === section && request.action === action) {
        returned = true;
        return structuredClone(result);
      }
      return original(request);
    };
  }, {section, action, result});
}

test("failed Memory save keeps the editor and draft intact, then retries cleanly", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("data-memory/memories"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill("Retry-safe browser memory");
  await panel.locator("#memory-category").fill("resilience");

  await failNextManagementCall(page, "memories", "add", "Transient memory save failure");
  await panel.locator("#memory-save").click();

  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#memory-error")).toHaveText("Unable to save memory: Transient memory save failure");
  await expect(panel.locator("#memory-content")).toHaveValue("Retry-safe browser memory");
  await expect(panel.locator("#memory-category")).toHaveValue("resilience");
  expect(await page.evaluate(() => window.browserHarness.getState().memories)).toHaveLength(1);

  await panel.locator("#memory-save").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);
  await expect(panel.getByText("Retry-safe browser memory", {exact: true})).toBeVisible();

  const memories = await page.evaluate(() => window.browserHarness.getState().memories);
  expect(memories).toHaveLength(2);
  expect(memories.filter((memory) => memory.content === "Retry-safe browser memory")).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});

test("failed Request Rule save preserves the draft and backend, then retries cleanly", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));

  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Request Rules", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Create rule", exact: true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#rule-name").fill("Retry-safe browser rule");
  await panel.locator("#rule-phrases").fill("retry safe route");
  await panel.locator("#rule-match").selectOption("contains");
  await panel.locator("#rule-action-type").selectOption("model_routing");
  await panel.locator("#rule-model").fill("gpt-5-mini");
  await panel.locator("#rule-reasoning").selectOption("medium");
  await panel.locator("#rule-scope").selectOption("request");

  await failNextManagementCall(page, "request_rules", "create", "Transient Request Rule save failure");
  await panel.locator("#rule-save").click();

  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#rule-error")).toHaveText("Transient Request Rule save failure");
  await expect(panel.locator("#rule-name")).toHaveValue("Retry-safe browser rule");
  await expect(panel.locator("#rule-phrases")).toHaveValue("retry safe route");
  expect(await page.evaluate(() => window.browserHarness.getState().requestRules.rules)).toHaveLength(1);

  await panel.locator("#rule-save").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
  await expect(panel.getByRole("heading", {name: "Retry-safe browser rule", exact: true})).toBeVisible();

  const rules = await page.evaluate(() => window.browserHarness.getState().requestRules.rules);
  expect(rules).toHaveLength(2);
  expect(rules.filter((rule) => rule.name === "Retry-safe browser rule")).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});

test("malformed Function Tool YAML stays editable and cannot mutate persisted tools before correction", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  const malformed = "spec:\n  description: Missing name on purpose\nfunction:\n  type: script\n  sequence: []\n";
  await panel.locator("#tool-yaml").fill(malformed);
  await returnNextManagementResult(page, "tools", "save", {
    valid: false,
    errors: {"functions[0].spec.name": "is required"},
  });
  await panel.locator("#tool-save").click();

  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#tool-error")).toContainText("functions[0].spec.name: is required");
  await expect(panel.locator("#tool-yaml")).toHaveValue(malformed);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.config.functions)).toHaveLength(1);

  await panel.locator("#tool-yaml").fill(browserToolYaml("Corrected after validation"));
  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Corrected after validation");
  const tools = await page.evaluate(() => window.browserHarness.getState().configuration.config.functions);
  expect(tools).toHaveLength(2);
  expect(tools.filter((tool) => tool.spec?.name === "browser_tool")).toHaveLength(1);
  await expectHarnessClean(page, pageErrors);
});

test("server-rejected Function Tool save preserves YAML and persisted state, then retries once", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  const draft = browserToolYaml("Server rejection draft");
  await panel.locator("#tool-yaml").fill(draft);
  await failNextManagementCall(page, "tools", "save", "Function Tool name already exists");
  await panel.locator("#tool-save").click();

  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#tool-error")).toHaveText("Function Tool name already exists");
  await expect(panel.locator("#tool-yaml")).toHaveValue(draft);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.config.functions)).toHaveLength(1);

  await panel.locator("#tool-save").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", false);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.config.functions)).toHaveLength(2);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toContainText("Server rejection draft");
  await expectHarnessClean(page, pageErrors);
});

test("server-rejected Function Group save preserves every field and membership before retry", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await panel.locator("#function-add").click();
  await panel.locator("#add-group").click();
  await panel.locator("#group-name").fill("Rejected browser group");
  await panel.locator("#group-id").fill("rejected-browser-group");
  await panel.locator("#group-description").fill("Keep this complete draft after rejection");
  await panel.locator('#group-functions input[value="baseline_tool"]').check();
  await failNextManagementCall(page, "tools", "save_group", "Function Group ID already exists");
  await panel.locator("#group-save").click();

  await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#group-error")).toHaveText("Function Group ID already exists");
  await expect(panel.locator("#group-name")).toHaveValue("Rejected browser group");
  await expect(panel.locator("#group-id")).toHaveValue("rejected-browser-group");
  await expect(panel.locator("#group-description")).toHaveValue("Keep this complete draft after rejection");
  await expect(panel.locator('#group-functions input[value="baseline_tool"]')).toBeChecked();
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.config.function_groups)).toHaveLength(1);

  await panel.locator("#group-save").click();
  await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", false);
  const groups = await page.evaluate(() => window.browserHarness.getState().configuration.config.function_groups);
  expect(groups).toHaveLength(2);
  expect(groups.filter((group) => group.id === "rejected-browser-group")).toHaveLength(1);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('.function-group-card[data-group-id="rejected-browser-group"]')).toContainText("Rejected browser group");
  await expectHarnessClean(page, pageErrors);
});

test("server-rejected general configuration save remains dirty and retries without losing the draft", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&fail_config_once=1"));

  let panel = page.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect.poll(() => page.evaluate(() => {
    const current = document.querySelector("extended-openai-management-panel");
    return Boolean(current?._configData?.config && current?._draft
      && current?._draftAgentId === current?._agentId && !current?._busy);
  })).toBe(true);
  await expect(title).toHaveValue("Jarvis");
  await title.fill("Retry-safe agent title");
  await expect(title).toHaveValue("Retry-safe agent title");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();

  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await expect(title).toHaveValue("Retry-safe agent title");
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Jarvis");

  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Retry-safe agent title");

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Retry-safe agent title");
  await expectHarnessClean(page, pageErrors);
});

test("browser document closure at request boundaries preserves authoritative state without replay", async ({page}) => {
  test.setTimeout(30000);
  const context = page.context();
  let activePage = page;
  const initialTitle = "Jarvis";
  const savedTitle = "Committed before browser document closed";
  await activePage.goto(fixtureUrl("assistant/basics"));
  let panel = activePage.locator("extended-openai-management-panel");
  const title = panel.locator('[data-config="__title"]');
  await expect.poll(() => activePage.evaluate(() => {
    const current = document.querySelector("extended-openai-management-panel");
    return Boolean(current?._configData?.config && !current._busy);
  })).toBe(true);

  for (const stage of ["before-send", "request-sent", "committed-before-ack"]) {
    if (stage !== "before-send") {
      await title.fill(savedTitle);
      await activePage.evaluate((currentStage) => {
        const hass = window.browserHarness.hass;
        const original = hass.callWS.bind(hass);
        hass.callWS = async (request) => {
          if (request.section !== "configuration" || request.action !== "update") {
            return original(request);
          }
          const key = `eoc-document-operation-count-${currentStage}`;
          const operations = Number(localStorage.getItem(key) || 0) + 1;
          localStorage.setItem(key, String(operations));
          if (currentStage === "request-sent") {
            window.__eocDocumentStage = "request-sent";
            await new Promise(() => {});
          }
          const result = await original(request);
          window.__eocDocumentStage = "backend-committed-ack-held";
          await new Promise(() => {});
          return result;
        };
      }, stage);
      await panel.getByRole("button", {name: "Save changes", exact: true}).click();
      const expectedStage = stage === "request-sent" ? "request-sent" : "backend-committed-ack-held";
      await expect.poll(() => activePage.evaluate(() => window.__eocDocumentStage || "")).toBe(expectedStage);
    } else {
      await title.fill("Draft never sent");
    }

    await activePage.close();
    activePage = await context.newPage();
    await activePage.goto(fixtureUrl("assistant/basics"));
    panel = activePage.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue(
      stage === "committed-before-ack" ? savedTitle : initialTitle,
    );
    const operationCount = await activePage.evaluate(
      (currentStage) => Number(localStorage.getItem(`eoc-document-operation-count-${currentStage}`) || 0),
      stage,
    );
    expect(operationCount).toBe(stage === "before-send" ? 0 : 1);
    await expect(panel.getByRole("alert")).toHaveCount(0);
    if (stage !== "committed-before-ack") await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);
  }
  await activePage.close();
});

test("management remains usable when agent preference storage writes throw", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.addInitScript((failureName) => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "extended-openai-agent" || key === "extended-openai-entry") {
        throw new DOMException("Agent preference storage is unavailable", failureName);
      }
      return original.call(this, key, value);
    };
  }, "QuotaExceededError");
  await page.goto(fixtureUrl("assistant/basics", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await panel.locator("#agent").selectOption("scale-agent-1");
  await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
  await panel.locator('[data-config="__title"]').fill("Works without local storage");
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.getState().configuration.title))
    .toBe("Works without local storage");
  await expectHarnessClean(page, pageErrors);
});

test("clearing browser storage while management is open does not interrupt editing", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("assistant/basics", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#agent")).toHaveValue("agent-1");
  await page.evaluate(() => localStorage.clear());
  await panel.locator("#agent").selectOption("scale-agent-1");
  await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
  await panel.locator('[data-config="__title"]').fill("Works after storage was cleared");
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.browserHarness.getState().configuration.title))
    .toBe("Works after storage was cleared");
  await expectHarnessClean(page, pageErrors);
});

test("malformed backup import leaves persisted state untouched and a valid retry restores cleanly", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));

  let panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const backup = await downloadPromise;
  const backupPath = await backup.path();
  expect(backupPath).toBeTruthy();

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Changed before malformed restore");
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Changed before malformed restore");

  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#backup-file-transfer").setInputFiles({
    name: "malformed-backup.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("not a valid backup document"),
  });

  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator("#restore-transfer-apply")).toBeDisabled();
  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Changed before malformed restore");

  await panel.locator("#backup-file-transfer").setInputFiles(backupPath);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#restore-backup-name")).toHaveText("Jarvis");
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);

  expect(await page.evaluate(() => window.browserHarness.getState().configuration.title)).toBe("Jarvis");
  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expectHarnessClean(page, pageErrors);
});
