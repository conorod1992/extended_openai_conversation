import {expect, test} from "@playwright/test";

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
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
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
    return /^\/extended_openai_conversation_responses\/assets\/[^/]+\/management-panel\.js$/.test(path)
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
  await expect(panel.getByRole("heading", {name: "Knowledge Library", exact: true})).toBeVisible();

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
  await expect(panel.getByRole("heading", {name: "Knowledge Library", exact: true})).toBeVisible();

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
