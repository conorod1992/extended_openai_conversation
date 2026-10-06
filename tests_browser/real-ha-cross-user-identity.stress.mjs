import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const switchBackend = process.env.REAL_HA_IDENTITY_SWITCH_BACKEND;
const switchControl = process.env.REAL_HA_IDENTITY_SWITCH_CONTROL;
const permissionBackend = process.env.REAL_HA_PERMISSION_BACKEND;
const permissionControl = process.env.REAL_HA_PERMISSION_CONTROL;
const raceBackendA = process.env.REAL_HA_ADMIN_RACE_BACKEND_A;
const raceBackendB = process.env.REAL_HA_ADMIN_RACE_BACKEND_B;

const fixture = (backend, route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backend)}`;

async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    body: JSON.stringify(body),
  });
  expect(response.ok).toBeTruthy();
  return response.json();
}

test("one browser profile does not carry private EOAI state from user A into user B", async ({page}) => {
  test.skip(!switchBackend || !switchControl, "requires switchable genuine HA identities");
  test.setTimeout(120_000);
  const errors = trackPageErrors(page);

  await page.goto(fixture(switchBackend, "data-memory/memories"));
  let panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();

  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Private same-browser marker A");
  await panel.locator("#memory-category").fill("identity-a");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toBeVisible();

  // Leave a browser-local draft open as well as committed private data.
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Unsaved private draft A");
  await page.evaluate(url => postMessage({noop:true}, "*"));
  await post(switchControl, {identity: "b"});

  // Same page/context/profile, but a fresh HA-authenticated identity.
  await page.goto(fixture(switchBackend, "data-memory/memories"));
  panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toHaveCount(0);
  await expect(panel.locator("#memory-dialog")).not.toBeVisible();
  await expect(panel.getByText("Unsaved private draft A", {exact: true})).toHaveCount(0);

  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("Private same-browser marker B");
  await panel.locator("#memory-category").fill("identity-b");
  await panel.locator("#memory-save").click();
  await expect(panel.getByText("Private same-browser marker B", {exact: true})).toBeVisible();
  await expect(panel.getByText("Private same-browser marker A", {exact: true})).toHaveCount(0);
  await expectHarnessClean(page, errors);
});

test("an already-authenticated browser obeys live HA admin downgrade and restoration", async ({page}) => {
  test.skip(!permissionBackend || !permissionControl, "requires mutable genuine HA user permissions");
  test.setTimeout(120_000);
  const errors = trackPageErrors(page);

  await page.goto(fixture(permissionBackend, "assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();

  const request = await page.evaluate(() => {
    const call = browserHarness.calls.find(item => item.section === "configuration" && item.action === "get");
    return {
      type: "extended_openai_conversation_responses/management",
      entry_id: call.entry_id,
      subentry_id: call.subentry_id,
      section: "configuration",
      action: "get",
    };
  });

  await post(permissionControl, {admin: false});
  const denied = await page.evaluate(async message => {
    try {
      await browserHarness.hass.callWS(message);
      return {ok: true};
    } catch (error) {
      return {ok: false, message: String(error?.message || error)};
    }
  }, request);
  expect(denied.ok).toBe(false);
  expect(denied.message).toMatch(/administrator permission is required/i);

  await post(permissionControl, {admin: true});
  const restored = await page.evaluate(async message => {
    try {
      const result = await browserHarness.hass.callWS(message);
      return {ok: true, title: result?.config?.__title || result?.config?.title || ""};
    } catch (error) {
      return {ok: false, message: String(error?.message || error)};
    }
  }, request);
  expect(restored.ok).toBe(true);
  await expectHarnessClean(page, errors);
});

test("two separately authenticated admins cannot overwrite the same stale Request Rule", async ({browser}) => {
  test.skip(!raceBackendA || !raceBackendB, "requires two independent genuine HA admin sessions");
  test.setTimeout(120_000);

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const errorsA = trackPageErrors(pageA);
  const errorsB = trackPageErrors(pageB);

  try {
    await pageA.goto(fixture(raceBackendA, "capabilities/request-rules"));
    const panelA = pageA.locator("extended-openai-management-panel");
    await panelA.getByRole("button", {name: "Create rule", exact: true}).first().click();
    await panelA.locator("#rule-name").fill("Cross-admin shared rule");
    await panelA.locator("#rule-phrases").fill("cross admin phrase");
    await panelA.locator("#rule-action-type").selectOption("model_routing");
    await panelA.locator("#rule-model").fill("gpt-5-mini");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "Cross-admin shared rule", exact: true})).toBeVisible();

    await pageB.goto(fixture(raceBackendB, "capabilities/request-rules"));
    const panelB = pageB.locator("extended-openai-management-panel");
    const cardA = panelA.locator(".request-rule-card").filter({hasText: "Cross-admin shared rule"});
    const cardB = panelB.locator(".request-rule-card").filter({hasText: "Cross-admin shared rule"});
    await expect(cardB).toBeVisible();
    await cardA.locator(".rule-edit").click();
    await cardB.locator(".rule-edit").click();

    await panelA.locator("#rule-name").fill("Cross-admin authoritative A");
    await panelA.locator("#rule-save").click();
    await expect(panelA.getByRole("heading", {name: "Cross-admin authoritative A", exact: true})).toBeVisible();

    await panelB.locator("#rule-name").fill("Cross-admin stale B");
    await panelB.locator("#rule-save").click();
    await expect(panelB.getByText(/changed in another tab/i).first()).toBeVisible();
    await expect(panelB.locator("#rule-name")).toHaveValue("Cross-admin stale B");

    await pageB.goto(fixture(raceBackendB, "capabilities/request-rules"));
    await expect(panelB.getByRole("heading", {name: "Cross-admin authoritative A", exact: true})).toBeVisible();
    await expect(panelB.getByRole("heading", {name: "Cross-admin stale B", exact: true})).toHaveCount(0);
    await expectHarnessClean(pageA, errorsA);
    expect(errorsB.windowErrors).toEqual([]);
    expect(errorsB.rejections).toEqual([]);
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
