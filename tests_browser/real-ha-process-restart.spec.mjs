import fs from "node:fs";
import path from "node:path";
import {expect, test} from "@playwright/test";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;
const syncDir = process.env.REAL_HA_RESTART_SYNC_DIR;

test.skip(
  !baseUrl || !authDataRaw || !syncDir,
  "requires the genuine Home Assistant process-restart harness",
);

function marker(name) {
  return path.join(syncDir, name);
}

async function waitForMarker(name, timeout = 45_000) {
  await expect.poll(() => fs.existsSync(marker(name)), {timeout}).toBe(true);
}

async function settleGenuineHaRoute(page) {
  const confirmHttpSettings = page.getByRole("button", {name: "Confirm", exact: true});

  // Enter the registered HA panel root first. Home Assistant owns creation of the
  // custom panel element; its deeper paths are internal routes handled by the
  // management panel after that element has initialized.
  await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("home-assistant")).toHaveCount(1);

  // A fresh staged HA profile may briefly present its one-time HTTP confirmation.
  // Acknowledge it if present, then re-enter the registered panel root because HA
  // may rebuild or navigate its shell while dismissing the dialog.
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

  // Move to Assistant/Basics through the panel's real navigation handler rather
  // than cold-loading a deep URL before the custom panel has established state.
  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  await expect(page).toHaveURL(/\/extended-openai\/assistant\/basics$/);
  await expect(panel.locator('[data-config="__title"]')).toBeVisible({timeout: 30_000});
  return panel;
}

test("open Home Assistant page survives a real backend process death and restart", async ({context, page}) => {
  const authData = JSON.parse(authDataRaw);
  const integrationPageErrors = [];

  page.on("pageerror", (error) => {
    const detail = [error.name, error.message, error.stack].filter(Boolean).join("\n");
    if (detail.includes("/extended_openai_conversation_responses/")) {
      integrationPageErrors.push(detail);
    }
  });

  await context.addInitScript((tokens) => {
    window.localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, authData);

  const panel = await settleGenuineHaRoute(page);

  const title = panel.locator('[data-config="__title"]');
  await title.fill("Before real HA restart");
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  const sentinel = await page.evaluate(() => {
    window.__extendedOpenAIRestartSentinel = crypto.randomUUID();
    return window.__extendedOpenAIRestartSentinel;
  });

  fs.writeFileSync(marker("browser-ready"), "ready\n");
  await waitForMarker("ha-dead");

  await expect.poll(
    async () => page.evaluate(async (url) => {
      try {
        await fetch(`${url}/api/`, {cache: "no-store"});
        return "reachable";
      } catch {
        return "offline";
      }
    }, baseUrl),
    {timeout: 15_000},
  ).toBe("offline");

  // Do not restart HA until Chromium has positively observed the outage.
  fs.writeFileSync(marker("browser-confirmed-offline"), "offline\n");
  await waitForMarker("ha-restarted");

  // No page.reload() is allowed. The same document must survive the websocket
  // outage and recover when HA returns on the same origin.
  await expect.poll(
    () => page.evaluate(() => window.__extendedOpenAIRestartSentinel),
    {timeout: 30_000},
  ).toBe(sentinel);

  // Prove the Home Assistant shell's existing websocket connection object has
  // recovered before exercising the integration again.
  await expect.poll(
    async () => page.evaluate(async () => {
      const hass = document.querySelector("home-assistant")?.hass;
      if (!hass?.callWS) {
        return false;
      }
      try {
        await hass.callWS({type: "get_config"});
        return true;
      } catch {
        return false;
      }
    }),
    {timeout: 45_000},
  ).toBe(true);

  await expect(panel.locator('[data-config="__title"]')).toBeVisible({timeout: 30_000});
  await expect(title).toHaveValue("Before real HA restart", {timeout: 30_000});

  await title.fill("After real HA restart");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Save configuration", exact: true}).click();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0, {timeout: 30_000});

  // A read after the post-restart write proves the recovered panel is not merely
  // rendering stale pre-crash state.
  await expect(title).toHaveValue("After real HA restart");
  await expect(panel.locator("#agent option:checked")).toHaveText("After real HA restart");

  expect(integrationPageErrors).toEqual([]);
  fs.writeFileSync(marker("browser-complete"), "complete\n");
});