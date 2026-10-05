import {expect, test} from "@playwright/test";
import {openColdHaRoute} from "./real-ha-shell-helpers.mjs";
import {waitForManagementRouteReady} from "../ci/frontend_latency/routes.mjs";

test.skip(process.env.EOAI_HTTPS_PROXY !== "1", "dedicated genuine HTTPS reverse-proxy acceptance");

test("EOAI remains usable through the genuine HA frontend behind HTTPS", async ({context, page}) => {
  test.setTimeout(120000);
  const sockets = [];
  page.on("websocket", socket => sockets.push(socket.url()));

  const panel = await openColdHaRoute(context, page, "assistant/basics");
  expect(new URL(page.url()).protocol).toBe("https:");
  const title = panel.locator('[data-config="__title"]');
  const value = `HTTPS proxy acceptance ${process.env.STRESS_SEED || "0"}`;
  await title.fill(value);
  await panel.getByRole("button", {name:"Save changes", exact:true}).click();
  await expect(panel.getByText("Unsaved changes", {exact:true})).toHaveCount(0);
  await page.reload({waitUntil:"domcontentloaded"});
  await expect(title).toHaveValue(value);
  expect(sockets.some(url => url.startsWith("wss://"))).toBe(true);

  await panel.evaluate(host => host._navigate("usage-maintenance", "backup-restore"));
  await waitForManagementRouteReady(
    page,
    {name:"backup-restore", path:"usage-maintenance/backup-restore"},
    30000,
  );
  await panel.locator("#transfer-export-mode").selectOption("full");
  const downloaded = page.waitForEvent("download");
  await panel.locator("#create-backup-transfer").click();
  const archive = await downloaded;
  expect(await archive.failure()).toBeNull();
  const path = await archive.path();
  expect(path).toBeTruthy();

  await panel.locator("#backup-file-transfer").setInputFiles(path);
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#restore-transfer-apply")).toBeEnabled();
  await panel.locator("#restore-transfer-cancel").click();
  await expect(panel.locator("#restore-dialog")).toHaveJSProperty("open", false);

  const fresh = await context.newPage();
  try {
    const reopened = await openColdHaRoute(context, fresh, "assistant/basics");
    await expect(reopened.locator('[data-config="__title"]')).toHaveValue(value);
    expect(new URL(fresh.url()).protocol).toBe("https:");
  } finally {
    await fresh.close();
  }
});
