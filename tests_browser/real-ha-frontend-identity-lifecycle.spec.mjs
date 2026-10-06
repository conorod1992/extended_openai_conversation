import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const lifecycleBackend = process.env.REAL_HA_BACKEND_URL;
const backendA = process.env.REAL_HA_BACKEND_URL_A;
const backendB = process.env.REAL_HA_BACKEND_URL_B;

const fixture = (backend, route) =>
  `/tests_browser/real-ha-fixture.html?route=${encodeURIComponent(route)}&backend=${encodeURIComponent(backend)}`;

async function openMemories(page, backend) {
  await page.goto(fixture(backend, "data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  await expect(panel.locator("#add-memory")).toBeVisible();
  return panel;
}

async function addMemory(panel, content, category) {
  await panel.locator("#add-memory").click();
  await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", true);
  await panel.locator("#memory-content").fill(content);
  await panel.locator("#memory-category").fill(category);
  await panel.locator("#memory-save").click();
}

test("committed Memory acknowledgement crossing EOAI unload/reload is not replayed", async ({page}) => {
  test.skip(!lifecycleBackend, "requires the gated genuine HA lifecycle bridge");
  test.setTimeout(120_000);
  const pageErrors = trackPageErrors(page);
  const marker = "Browser lifecycle boundary memory";

  const panel = await openMemories(page, lifecycleBackend);
  await addMemory(panel, marker, "lifecycle-boundary");

  // The bridge withholds the successful acknowledgement until EOAI has been
  // genuinely unloaded and set up again. The same browser document must settle
  // that one operation without a duplicate mutation or a false failure.
  await expect(panel.locator("#memory-dialog")).not.toBeVisible({timeout: 60_000});
  await expect(panel.getByText(marker, {exact: true})).toBeVisible({timeout: 60_000});
  await expect(panel.locator("#toast")).not.toContainText(/failed|error/i);

  const addsBeforeReload = await page.evaluate(() =>
    browserHarness.calls.filter(
      item => item.section === "memories" && item.action === "add",
    ).length,
  );
  expect(addsBeforeReload).toBe(1);

  await page.goto(fixture(lifecycleBackend, "data-memory/memories"));
  const reloadedPanel = page.locator("extended-openai-management-panel");
  await expect(reloadedPanel.getByText(marker, {exact: true})).toHaveCount(1);

  const addsAfterReload = await page.evaluate(() =>
    browserHarness.calls.filter(
      item => item.section === "memories" && item.action === "add",
    ).length,
  );
  expect(addsAfterReload).toBe(1);
  await expectHarnessClean(page, pageErrors);
});

test("separate browser sessions stay scoped to their authenticated HA users", async ({browser}) => {
  test.skip(!backendA || !backendB, "requires two authenticated genuine HA bridges");
  test.setTimeout(120_000);

  const contextA = await browser.newContext();
  const contextB = await browser.newContext();
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  const errorsA = trackPageErrors(pageA);
  const errorsB = trackPageErrors(pageB);

  const markerA = "Private browser memory for user A";
  const markerB = "Private browser memory for user B";

  try {
    const panelA = await openMemories(pageA, backendA);
    const panelB = await openMemories(pageB, backendB);

    await addMemory(panelA, markerA, "browser-user-a");
    await expect(panelA.getByText(markerA, {exact: true})).toBeVisible();
    await expect(panelA.getByText(markerB, {exact: true})).toHaveCount(0);

    await addMemory(panelB, markerB, "browser-user-b");
    await expect(panelB.getByText(markerB, {exact: true})).toBeVisible();
    await expect(panelB.getByText(markerA, {exact: true})).toHaveCount(0);

    // Reload both independently so this also checks that no process-global
    // frontend cache, selected scope, or prior response crosses browser contexts.
    await pageA.goto(fixture(backendA, "data-memory/memories"));
    await pageB.goto(fixture(backendB, "data-memory/memories"));
    const reloadedA = pageA.locator("extended-openai-management-panel");
    const reloadedB = pageB.locator("extended-openai-management-panel");

    await expect(reloadedA.getByText(markerA, {exact: true})).toHaveCount(1);
    await expect(reloadedA.getByText(markerB, {exact: true})).toHaveCount(0);
    await expect(reloadedB.getByText(markerB, {exact: true})).toHaveCount(1);
    await expect(reloadedB.getByText(markerA, {exact: true})).toHaveCount(0);

    expect(
      await pageA.evaluate(() =>
        browserHarness.calls.filter(
          item => item.section === "memories" && item.action === "add",
        ).length,
      ),
    ).toBe(1);
    expect(
      await pageB.evaluate(() =>
        browserHarness.calls.filter(
          item => item.section === "memories" && item.action === "add",
        ).length,
      ),
    ).toBe(1);

    await expectHarnessClean(pageA, errorsA);
    await expectHarnessClean(pageB, errorsB);
  } finally {
    await contextA.close();
    await contextB.close();
  }
});
