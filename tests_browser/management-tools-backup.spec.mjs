import {expect, test} from "@playwright/test";
import {acceptConfirmation, browserToolYaml, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

test("Escape cannot hide a committed restore's completion or refreshed configuration", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloading = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const path = await (await downloading).path();
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await panel.locator('[data-config="__title"]').fill("Changed after export");
  await panel.getByRole("button", {name:"Save changes", exact:true}).click();
  await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);
  await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    browserHarness.hass.callWS = async message => {
      const result = await original(message);
      if (message.action === "import_restore") {
        window.restoreCommitted = true;
        await new Promise(resolve => { window.releaseRestore = resolve; });
      }
      return result;
    };
  });
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect.poll(() => page.evaluate(() => window.restoreCommitted)).toBe(true);
  await expect(panel.locator("#restore-transfer-cancel")).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await page.evaluate(() => window.releaseRestore());
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  await expect(panel.getByText("Selected sections restored", {exact:true})).toBeVisible();
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expectHarnessClean(page, errors);
});

test("Escape retires the original agent's prepared import before switching agents", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore", "&agents=2"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloading = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const path = await (await downloading).path();
  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  const original = await panel.evaluate(host => host._backupTransferSession);
  await page.keyboard.press("Escape");
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  await expect.poll(() => panel.evaluate(host => host._backupTransferSession)).toBeNull();
  await expect.poll(() => page.evaluate(session => browserHarness.calls.some(call => call.action === "import_cancel" && call.data.session_id === session.session_id && call.entry_id === session.owner.entry_id && call.subentry_id === session.owner.subentry_id), original)).toBe(true);
  await panel.locator("#agent").selectOption("scale-agent-1");
  await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
  await expect.poll(() => panel.evaluate(host => host._agentId === "scale-agent-1" && !host._busy)).toBe(true);
  await expect(panel.locator("#backup-file-transfer")).toBeAttached();
  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  expect(await panel.evaluate(host => host._backupTransferSession.owner.subentry_id)).toBe("scale-agent-1");
  await panel.locator("#restore-transfer-cancel").click();
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  await expectHarnessClean(page, errors);
});

test("Function Tools support create, reload, edit, and delete through YAML", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  await panel.locator("#function-add").click();
  await panel.locator("#add-tool").click();
  await expect(panel.locator("#tool-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#tool-yaml").fill(browserToolYaml());
  await panel.locator("#tool-save").click();
  let card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Browser journey tool");

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Browser journey tool");
  await card.locator(".edit-tool").click();
  await expect(panel.locator("#tool-yaml")).toHaveValue(/Browser journey tool/);
  await panel.locator("#tool-yaml").fill(browserToolYaml("Browser journey tool edited"));
  await panel.locator("#tool-save").click();
  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Browser journey tool edited");

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".tool-card").filter({hasText: "browser_tool"});
  await expect(card).toContainText("Browser journey tool edited");
  await card.locator(".delete-tool").click();
  await acceptConfirmation(panel);
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toHaveCount(0);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator(".tool-card").filter({hasText: "browser_tool"})).toHaveCount(0);
  await expect(panel.locator(".tool-card").filter({hasText: "baseline_tool"})).toHaveCount(1);
  await expectHarnessClean(page, pageErrors);
});

test("Function Groups persist membership across create, reload, edit, and delete", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/functions"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Function Tools & Groups", exact: true})).toBeVisible();
  await panel.locator("#function-add").click();
  await panel.locator("#add-group").click();
  await expect(panel.locator("#group-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#group-name").fill("Browser group");
  await panel.locator("#group-id").fill("browser-group");
  await panel.locator("#group-description").fill("Browser group description");
  await panel.locator('#group-functions input[value="baseline_tool"]').check();
  await panel.locator("#group-save").click();
  let group = panel.locator('.function-group-card[data-group-id="browser-group"]');
  await expect(group).toContainText("Browser group");

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  group = panel.locator('.function-group-card[data-group-id="browser-group"]');
  await expect(group).toContainText("Browser group description");
  await group.locator("summary").click();
  await expect(group.locator(".tool-card").filter({hasText: "baseline_tool"})).toBeVisible();
  await group.locator(".edit-group").click();
  await panel.locator("#group-name").fill("Browser group edited");
  await panel.locator("#group-description").fill("Browser group description edited");
  await panel.locator("#group-save").click();

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  group = panel.locator('.function-group-card[data-group-id="browser-group"]');
  await expect(group).toContainText("Browser group edited");
  await expect(group).toContainText("Browser group description edited");
  await group.locator(".delete-group").click();
  await acceptConfirmation(panel);
  await expect(panel.locator('.function-group-card[data-group-id="browser-group"]')).toHaveCount(0);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('.function-group-card[data-group-id="browser-group"]')).toHaveCount(0);
  await expect(panel.locator(".tool-card").filter({hasText: "baseline_tool"})).toBeVisible();
  await expectHarnessClean(page, pageErrors);
});

test("full backup and restore round-trips settings, memories, rules, tools, and groups", async ({page}) => {
  const pageErrors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));

  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#create-backup-transfer")).toBeVisible();
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const download = await downloadPromise;
  const backupPath = await download.path();
  expect(backupPath).toBeTruthy();

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator('[data-config="__title"]').fill("Changed after backup");
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  let card = panel.locator(".list-card").filter({hasText: "Baseline browser fixture memory"});
  await card.locator(".delete-memory").click();
  await acceptConfirmation(panel);

  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  card = panel.locator(".request-rule-card").filter({hasText: "Baseline rule"});
  await card.locator(".rule-delete").click();
  await acceptConfirmation(panel);

  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  let group = panel.locator('.function-group-card[data-group-id="baseline-group"]');
  await group.locator(".delete-group").click();
  await acceptConfirmation(panel);
  card = panel.locator(".tool-card").filter({hasText: "baseline_tool"});
  await card.locator(".delete-tool").click();
  await acceptConfirmation(panel);

  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  panel = page.locator("extended-openai-management-panel");
  await panel.locator("#backup-file-transfer").setInputFiles(backupPath);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#restore-backup-name")).toHaveText("Jarvis");
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);

  await page.goto(fixtureUrl("assistant/basics"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await page.goto(fixtureUrl("data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByText("Baseline browser fixture memory", {exact: true})).toBeVisible();
  await page.goto(fixtureUrl("capabilities/request-rules"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Baseline rule", exact: true})).toBeVisible();
  await page.goto(fixtureUrl("capabilities/functions"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('.function-group-card[data-group-id="baseline-group"]')).toBeVisible();
  await expect(panel.locator(".tool-card").filter({hasText: "baseline_tool"})).toHaveCount(1);

  await expectHarnessClean(page, pageErrors);
});

test("restore warns about replacing an active configuration draft only when applying", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/backup-restore"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloadPromise = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const backupPath = await (await downloadPromise).path();
  await panel.locator('.top-nav button[data-page="assistant"]').click();
  await panel.locator('[data-config="__title"]').fill("Unsaved restore draft");
  await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();
  await panel.locator("#local-section").selectOption("backup-restore", {force: true});
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator("#transfer-export-mode")).toBeEnabled();
  await expect(panel.getByText("Exports use saved configuration.", {exact: false})).toBeVisible();
  await panel.locator("#backup-file-transfer").setInputFiles(backupPath);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-apply").click();
  await expect(panel.locator("#confirm-message")).toContainText("unsaved configuration changes will be replaced and lost");
  await panel.locator("#confirm-cancel").click();
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  expect(await page.evaluate(() => browserHarness.calls.filter((call) => call.action === "import_restore").length)).toBe(0);
  await panel.locator("#restore-transfer-apply").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);
  await panel.locator('.top-nav button[data-page="assistant"]').click();
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
  await expect(panel.locator("#dirty-state")).toHaveCount(0);
  await expectHarnessClean(page, errors);
});
