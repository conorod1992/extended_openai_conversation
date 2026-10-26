import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const recovered = panel => panel.getByRole("heading", {name:"Recovered unsaved editor"});

async function replacePanel(panel) {
  await panel.evaluate(host => {
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    const next = document.createElement("extended-openai-management-panel");
    parent.append(next);
    next.hass = hass;
  });
}

async function openRecoveredCopy(page, bundled) {
  await page.goto(fixtureUrl("guide", `&agents=2${bundled ? "&bundle=1" : ""}`));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator("#guide-search")).toBeVisible();
  await panel.evaluate(async host => {
    host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
    await host._navigate("assistant", "basics");
    await host._navigate("capabilities", "request-rules");
    await host._call("request_rules", "groups", {groups:[{id:"unsaved-destination", name:"Unsaved destination"}], revision:host._result.revision});
    await host._loadSection(true);
  });
  await panel.locator(".rule-edit").first().click();
  await panel.locator("#rule-name").fill("Recovered rule draft <keep exact text>");
  await panel.locator("#rule-group").selectOption("unsaved-destination");
  await panel.evaluate(async host => {
    window.recoveredCopySource = host;
    // Remove the destination through the real transport while keeping the
    // authored dialog. Recovery must offer its copy instead of silently rebasing.
    await host._call("request_rules", "groups", {groups:[], revision:host._result.revision});
  });
  await replacePanel(panel);
  await expect(recovered(panel)).toBeVisible();
  await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/Recovered rule draft <keep exact text>/);
  return panel;
}

for (const bundled of [false, true]) {
  test(`recovered-copy modal follows Back/Forward ownership (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openRecoveredCopy(page, bundled);
    await page.goBack();
    await expect.poll(() => panel.evaluate(host => host._viewKey())).toBe("assistant/basics");
    await expect(recovered(panel)).toHaveCount(0);
    await expect(panel.locator('[data-config="__title"]')).toBeVisible();
    expect(await page.evaluate(() => recoveredCopySource.shadowRoot.querySelector("#rule-dialog").open)).toBe(true);
    await page.goForward();
    await expect(recovered(panel)).toBeVisible();
    await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/Recovered rule draft <keep exact text>/);
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "update").length)).toBe(0);
    await expectHarnessClean(page, errors);
  });

  test(`recovered-copy modal retires on in-app navigation while retaining its source (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openRecoveredCopy(page, bundled);
    await panel.evaluate(host => {
      window.retiredRecoveryDiscard = [...host.shadowRoot.querySelectorAll("dialog button")].find(button => button.textContent === "Discard recovered edits");
      return host._navigate("guide");
    });
    await expect(recovered(panel)).toHaveCount(0);
    await expect(panel.locator("#guide-search")).toBeVisible();
    expect(await page.evaluate(() => retiredRecoveryDiscard.onclick)).toBe(null);
    expect(await page.evaluate(() => recoveredCopySource.shadowRoot.querySelector("#rule-dialog").open)).toBe(true);
    await panel.locator('.top-nav button[data-page="capabilities"]').click();
    await panel.locator("#local-section").selectOption("request-rules", {force:true});
    await expect(recovered(panel)).toBeVisible();
    await expect(panel.getByLabel("Recovered editor values")).toHaveValue(/unsaved-destination/);
    await expectHarnessClean(page, errors);
  });

  test(`assistant switch explicitly discards the retained recovered source (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openRecoveredCopy(page, bundled);
    await page.goBack();
    await expect(panel.locator('[data-config="__title"]')).toBeVisible();
    await panel.locator("#agent").selectOption("scale-agent-1");
    await acceptConfirmation(panel);
    await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
    await expect(recovered(panel)).toHaveCount(0);
    expect(await page.evaluate(() => Boolean(recoveredCopySource.shadowRoot.querySelector("#rule-dialog")?.open))).toBe(false);
    await panel.locator("#agent").selectOption("agent-1");
    await panel.locator('.top-nav button[data-page="capabilities"]').click();
    await panel.locator("#local-section").selectOption("request-rules", {force:true});
    await expect(panel.locator("#rule-add")).toBeVisible();
    await expect(recovered(panel)).toHaveCount(0);
    await expectHarnessClean(page, errors);
  });

  test(`two panel replacements retain the original recovered values (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openRecoveredCopy(page, bundled);
    const original = await panel.getByLabel("Recovered editor values").inputValue();
    for (let replacement = 0; replacement < 2; replacement++) {
      await replacePanel(panel);
      await expect(recovered(panel)).toBeVisible();
      await expect(panel.getByLabel("Recovered editor values")).toHaveValue(original);
    }
    await panel.getByRole("button", {name:"Discard recovered edits"}).click();
    await expect(recovered(panel)).toHaveCount(0);
    expect(await page.evaluate(() => recoveredCopySource.shadowRoot.querySelector("#rule-dialog").open)).toBe(false);
    await replacePanel(panel);
    await expect(panel.locator("#rule-add")).toBeVisible();
    await expect(recovered(panel)).toHaveCount(0);
    await expectHarnessClean(page, errors);
  });
}
