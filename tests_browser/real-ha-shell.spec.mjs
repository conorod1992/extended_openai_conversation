import {expect, test} from "@playwright/test";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;

test.skip(!baseUrl || !authDataRaw, "requires the dedicated genuine Home Assistant frontend-shell harness");

async function clearOptionalHttpConfirmation(page, panel) {
  const title = panel.locator('[data-config="__title"]');
  const confirmHttpSettings = page.getByRole("button", {name: "Confirm", exact: true});

  // A fresh HA test profile may show the one-time HTTP server confirmation before
  // the custom panel finishes rendering. A reused/confirmed profile goes straight
  // to the panel. Accept either state rather than making the one-time dialog part
  // of the integration's acceptance contract.
  await expect.poll(async () => (
    await title.isVisible().catch(() => false)
    || await confirmHttpSettings.isVisible().catch(() => false)
  )).toBe(true);

  if (!await confirmHttpSettings.isVisible().catch(() => false)) {
    return;
  }

  await confirmHttpSettings.click();
  await expect(confirmHttpSettings).toHaveCount(0);

  // Confirming HA's HTTP settings can rebuild/navigate the shell. Re-enter the
  // integration route before asserting against the shipped management panel.
  await page.goto(`${baseUrl}/extended-openai/assistant/basics`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  await expect(panel).toHaveCount(1);
}

test("shipped management panel loads and persists configuration inside the genuine HA frontend", async ({context, page}) => {
  const authData = JSON.parse(authDataRaw);
  const integrationPageErrors = [];
  const integrationRequestFailures = [];
  const integrationResponses = [];

  page.on("pageerror", (error) => {
    const detail = [error.name, error.message, error.stack].filter(Boolean).join("\n");
    if (detail.includes("/extended_openai_conversation_responses/")) {
      integrationPageErrors.push(detail);
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

  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);

  await page.goto(`${baseUrl}/extended-openai/assistant/basics`, {waitUntil: "domcontentloaded"});

  // These two elements distinguish this acceptance path from the standalone
  // Playwright fixture: Home Assistant owns the shell and instantiates the
  // integration's registered custom panel inside it.
  await expect(page.locator("home-assistant")).toHaveCount(1);
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await clearOptionalHttpConfirmation(page, panel);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await expect(panel.locator('[data-config="chat_model"]')).toBeVisible();

  const title = panel.locator('[data-config="__title"]');
  await title.fill("Real HA shell saved");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  await page.reload({waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toHaveValue("Real HA shell saved");
  await expect(panel.locator("#agent option:checked")).toHaveText("Real HA shell saved");

  expect(integrationRequestFailures).toEqual([]);
  expect(integrationPageErrors).toEqual([]);
  expect(integrationResponses.some(({url, status}) => {
    const path = new URL(url).pathname;
    return /^\/extended_openai_conversation_responses\/assets\/[^/]+\/management-panel\.js$/.test(path)
      && status === 200;
  })).toBe(true);
  expect(integrationResponses.filter(({status}) => status >= 400)).toEqual([]);
});
