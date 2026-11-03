import {expect, test} from "@playwright/test";
import {fixtureUrl, acceptConfirmation, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const replacement of [false, true]) test(`Retention deletion retries a committed storage failure (${bundled ? "bundle" : "source"}, ${replacement ? "replacement" : "same panel"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("usage-maintenance/retention", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#config-usage_request_retention_days")).toBeVisible();
  await panel.evaluate(host => { host.hass = {...host.hass, user:{id:"retention-admin", is_admin:true}}; });
  await expect.poll(() => panel.evaluate(host => !host._busy && !host._eocCatalogBootstrap?.pending)).toBe(true);
  await panel.evaluate(host => {
    const send = host.hass.callWS.bind(host.hass);
    window.retentionAttempts = [];
    host.hass.callWS = async message => {
      const result = await send(message);
      if (message.section === "configuration" && message.action === "save") {
        retentionAttempts.push(structuredClone(message));
        if (retentionAttempts.length === 1) throw {
          code:"eoai_mutation_committed",
          message:"Configuration saved, but Usage retention could not be applied; retry saving retention to finish deletion.",
        };
      }
      return result;
    };
  });
  await panel.locator("#config-usage_request_retention_days").selectOption("0");
  await panel.locator("#save-config").click();
  await acceptConfirmation(panel);
  await expect(panel.locator("#toast")).toContainText("retry saving retention");
  await expect(panel.locator("#save-config")).toBeEnabled();
  await expect(panel.locator("#config-usage_request_retention_days")).toHaveValue("0");
  const firstRevision = await page.evaluate(() => retentionAttempts[0].revision);
  expect(await panel.evaluate(host => host._configData.revision)).not.toBe(firstRevision);
  if (replacement) {
    await panel.evaluate(host => {
      const next = document.createElement("extended-openai-management-panel");
      next.hass = host.hass;
      host.remove();
      document.body.append(next);
      window.browserHarness.panel = next;
    });
    await expect(panel.locator("#config-usage_request_retention_days")).toHaveValue("0");
    await expect(panel.locator("#save-config")).toBeEnabled();
    expect(await panel.evaluate(host => Boolean(host._eocPendingRetention))).toBe(true);
  }
  await panel.locator("#save-config").click();
  await expect(panel.locator("#toast")).toHaveText("Changes saved");
  await expect(panel.locator("#save-config")).toHaveCount(0);
  const attempts = await page.evaluate(() => retentionAttempts);
  expect(attempts).toHaveLength(2);
  expect(attempts[1].config).toEqual({usage_request_retention_days:0});
  expect(attempts[1].revision).not.toBe(firstRevision);
  await expectHarnessClean(page, errors);
});
