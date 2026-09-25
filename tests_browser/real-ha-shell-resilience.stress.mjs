import {expect, test} from "@playwright/test";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const originalAuth = process.env.REAL_HA_FRONTEND_AUTH;
const controlUrl = process.env.REAL_HA_AUTH_CONTROL_URL;
test.skip(!baseUrl || !originalAuth || !controlUrl, "requires scheduled/manual genuine HA shell acceptance");

async function openAssistant(page, context, auth = JSON.parse(originalAuth)) {
  await context.addInitScript(tokens => {
    if (!localStorage.getItem("hassTokens")) localStorage.setItem("hassTokens", JSON.stringify(tokens));
  }, auth);
  await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  const confirm = page.getByRole("button", {name: "Confirm", exact: true});
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.click();
    await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  }
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Extended OpenAI", exact: true})).toBeVisible({timeout: 30_000});
  await panel.getByRole("button", {name: "Assistant", exact: true}).click();
  const title = panel.locator('[data-config="__title"]');
  await expect(title).toBeVisible({timeout: 30_000});
  return {panel, title};
}

test("slow genuine HA WebSocket save never reports success before the backend replies", async ({page, context}) => {
  await page.addInitScript(() => {
    window.__managementSocketFault = {delayNext: 0, delayed: 0};
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function(payload) {
      let message;
      try { message = JSON.parse(payload); } catch { /* protocol frame */ }
      if (message?.type === "extended_openai_conversation_responses/management" && message?.section === "configuration" && message?.action === "save" && window.__managementSocketFault.delayNext) {
        const delay = window.__managementSocketFault.delayNext;
        window.__managementSocketFault.delayNext = 0;
        window.__managementSocketFault.delayed++;
        setTimeout(() => send.call(this, payload), delay);
        return;
      }
      return send.call(this, payload);
    };
  });
  const {panel, title} = await openAssistant(page, context);
  const value = `Slow HA WS ${process.env.STRESS_SEED || "0"}`;
  await title.fill(value);
  await page.evaluate(() => { window.__managementSocketFault.delayNext = 1000; });
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.__managementSocketFault.delayed)).toBe(1);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0, {timeout: 15_000});
  const fresh = await context.newPage();
  try {
    const reopened = await openAssistant(fresh, context);
    await expect(reopened.title).toHaveValue(value);
  } finally { await fresh.close(); }
});

test("revoked HA session while editing cannot commit a save", async ({page, context}) => {
  const {panel, title} = await openAssistant(page, context);
  const before = await title.inputValue();
  const draft = `Expired HA auth ${process.env.STRESS_SEED || "0"}`;
  await title.fill(draft);
  const replacement = await page.evaluate(url => fetch(url, {method: "POST"}).then(response => response.json()), controlUrl);
  expect(replacement.access_token).toBeTruthy();
  // Revocation closes the authenticated HA socket. Attempting the pending edit
  // must not be acknowledged as a successful save on the old session.
  await page.waitForTimeout(300);
  if (await panel.getByRole("button", {name: "Save changes", exact: true}).isVisible().catch(() => false)) {
    await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  }
  const recovery = await context.newPage();
  try {
    await recovery.addInitScript(tokens => localStorage.setItem("hassTokens", JSON.stringify(tokens)), replacement);
    const reopened = await openAssistant(recovery, context, replacement);
    await expect(reopened.title).toHaveValue(before);
  } finally { await recovery.close(); }
});

test("genuine HA WebSocket drop during a save leaves the backend authoritative", async ({page, context}) => {
  // The preceding expiry case revoked its token. Start this independent browser
  // journey with another genuine HA refresh/access token pair.
  const freshAuth = await fetch(controlUrl, {method: "POST"}).then(response => response.json());
  await page.addInitScript(() => {
    window.__dropManagementSave = false;
    window.__droppedManagementSave = 0;
    const send = WebSocket.prototype.send;
    WebSocket.prototype.send = function(payload) {
      let message;
      try { message = JSON.parse(payload); } catch { /* protocol frame */ }
      if (window.__dropManagementSave && message?.type === "extended_openai_conversation_responses/management" && message?.section === "configuration" && message?.action === "save") {
        window.__dropManagementSave = false;
        window.__droppedManagementSave++;
        this.close();
        return;
      }
      return send.call(this, payload);
    };
  });
  const {panel, title} = await openAssistant(page, context, freshAuth);
  const before = await title.inputValue();
  const draft = `Dropped HA WS ${process.env.STRESS_SEED || "0"}`;
  await title.fill(draft);
  await page.evaluate(() => { window.__dropManagementSave = true; });
  await panel.getByRole("button", {name: "Save changes", exact: true}).click();
  await expect.poll(() => page.evaluate(() => window.__droppedManagementSave)).toBe(1);
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();
  await expect(title).toHaveValue(draft);
  const fresh = await context.newPage();
  try {
    const reopened = await openAssistant(fresh, context);
    await expect(reopened.title).toHaveValue(before);
  } finally { await fresh.close(); }
  await page.goto(`${baseUrl}/extended-openai`, {waitUntil: "domcontentloaded"});
  await expect(page.locator("extended-openai-management-panel")).toHaveCount(1);
});
