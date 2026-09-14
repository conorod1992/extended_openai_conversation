import {expect, test} from "@playwright/test";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;

test.skip(!baseUrl || !authDataRaw, "requires the dedicated genuine Home Assistant frontend-shell harness");

async function expectRoute(page, panel, path, heading) {
  await expect(page).toHaveURL(`${baseUrl}${path}`);
  await expect(panel.getByRole("heading", {name: heading, exact: true})).toBeVisible();
}

test("genuine HA shell follows nested deep links and browser back-forward history", async ({context, page}) => {
  const authData = JSON.parse(authDataRaw);
  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);

  // Enter through a nested URL rather than the panel's default route. This proves
  // Home Assistant supplies the correct initial route to the shipped custom panel.
  await page.goto(`${baseUrl}/extended-openai/data-memory/knowledge`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel).toHaveCount(1);
  await expectRoute(page, panel, "/extended-openai/data-memory/knowledge", "Knowledge Library");

  // Build a real history stack through the panel's own navigation handlers.
  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expectRoute(page, panel, "/extended-openai/assistant/basics", "Assistant");

  await panel.getByRole("button", {name: "Capabilities", exact: true}).click();
  await expectRoute(page, panel, "/extended-openai/capabilities/home-assistant", "Home Assistant & local handling");

  await panel.getByRole("button", {name: "Functions", exact: true}).click();
  await expectRoute(page, panel, "/extended-openai/capabilities/functions", "Function Tools & Groups");

  // Browser history traversal is owned by Home Assistant. Each popstate must be
  // reflected back into the custom panel's route setter and repaint the section.
  await page.goBack();
  await expectRoute(page, panel, "/extended-openai/capabilities/home-assistant", "Home Assistant & local handling");
  await page.goBack();
  await expectRoute(page, panel, "/extended-openai/assistant/basics", "Assistant");
  await page.goBack();
  await expectRoute(page, panel, "/extended-openai/data-memory/knowledge", "Knowledge Library");

  await page.goForward();
  await expectRoute(page, panel, "/extended-openai/assistant/basics", "Assistant");
  await page.goForward();
  await expectRoute(page, panel, "/extended-openai/capabilities/home-assistant", "Home Assistant & local handling");
  await page.goForward();
  await expectRoute(page, panel, "/extended-openai/capabilities/functions", "Function Tools & Groups");
});
