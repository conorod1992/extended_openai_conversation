import {expect, test} from "@playwright/test";
import {acceptConfirmation, expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function reviewPolicy(panel, mode) {
  if (mode === "fresh") {
    await panel.locator("#guest-start-fresh").click();
    await acceptConfirmation(panel);
  } else await panel.locator("#guest-review-converted").click();
  await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toBeVisible();
}

for (const bundled of [false, true]) for (const mode of ["review", "fresh"]) {
  test(`obsolete Guest read cannot close another assistant's ${mode} policy editor (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/guest-mode", `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toBeVisible();
    await panel.evaluate(host => {
      const original=host.hass.callWS.bind(host.hass);
      window.guestOwner={};
      host.hass.callWS=async request => {
        const result=await original(request);
        if (request.section === "guest_mode" && request.action === "get") {
          if (request.subentry_id === "agent-1") {
            guestOwner.entered=true;
            await new Promise(resolve => {guestOwner.release=resolve;});
          } else result.legacy_policy=true;
        }
        return result;
      };
      guestOwner.read=host._loadSectionData();
    });
    await expect.poll(() => page.evaluate(() => guestOwner.entered)).toBe(true);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect(panel.locator("#guest-review-converted")).toBeVisible();
    await reviewPolicy(panel, mode);
    await panel.locator('[data-guest-mode="guest_function_policy"]').selectOption("custom");
    await page.evaluate(async () => {guestOwner.release(); await guestOwner.read;});
    // A committed edit causes an ordinary render after the obsolete read ends.
    await panel.locator('[data-guest-mode="guest_knowledge_policy"]').selectOption("custom");
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toHaveValue("custom");
    await expect(panel.locator("#guest-review-converted")).toHaveCount(0);
    expect(await panel.evaluate(host => host._guestStartingFresh || false)).toBe(mode === "fresh");
    expect(await panel.evaluate(host => host._guestDraft.guest_knowledge_policy)).toBe("custom");
    expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "guest_mode" && call.action === "save_policy").length)).toBe(0);
    await expectHarnessClean(page,errors);
  });

  test(`legacy Guest ${mode} draft stays editable through repeated replacement (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/guest-mode", bundled ? "&bundle=1" : ""));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toBeVisible();
    await page.evaluate(() => {
      const state=browserHarness.getState(); state.guest.legacy_policy=true;
      localStorage.setItem("extended-openai-browser-harness-state-v3",JSON.stringify(state));
    });
    await page.goto(fixtureUrl("capabilities/guest-mode", bundled ? "&bundle=1" : ""));
    await expect(panel.locator("#guest-review-converted")).toBeVisible();
    await panel.evaluate(host => {host.hass={...host.hass,user:{id:"admin",is_admin:true}};});
    await reviewPolicy(panel,mode);
    await panel.evaluate(host => host._loadSectionData());
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toBeVisible();
    await panel.locator('[data-guest-mode="guest_function_policy"]').selectOption("custom");
    await panel.evaluate(host => host._loadSectionData());
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toHaveValue("custom");
    await panel.evaluate(host => {void host._navigate("guide");});
    await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open",true);
    await panel.locator("#confirm-cancel").click();
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toHaveValue("custom");
    for (let replacement=0; replacement<2; replacement++) {
      await panel.evaluate(host => {
        const parent=host.parentElement, hass=host.hass; host.remove();
        const next=document.createElement("extended-openai-management-panel");
        parent.append(next); next.hass=hass;
      });
      await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toHaveValue("custom");
      expect(await panel.evaluate(host => host._guestStartingFresh || false)).toBe(mode === "fresh");
    }
    await panel.locator("#save-page").click();
    await expect(panel.locator("#save-page")).toHaveCount(0);
    expect(await page.evaluate(() => browserHarness.getState().guest.config.guest_function_policy)).toBe("custom");
    expect(await panel.evaluate(host => host._guestMigrationReview || false)).toBe(false);
    expect(await panel.evaluate(host => host._guestStartingFresh || false)).toBe(false);
    await expectHarnessClean(page,errors);
  });

  test(`Guest ${mode} state does not follow an assistant switch (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("capabilities/guest-mode", `&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toBeVisible();
    await panel.evaluate(async host => {
      const original=host.hass.callWS.bind(host.hass);
      host.hass.callWS=async request => {
        const result=await original(request);
        if(request.section === "guest_mode" && request.action === "get") result.legacy_policy=true;
        return result;
      };
      await host._loadSectionData();
    });
    await reviewPolicy(panel,mode);
    await panel.locator("#agent").selectOption("scale-agent-1");
    if(mode === "fresh") await acceptConfirmation(panel);
    await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
    await expect(panel.locator("#guest-review-converted")).toBeVisible();
    await expect(panel.locator('[data-guest-mode="guest_function_policy"]')).toHaveCount(0);
    expect(await panel.evaluate(host => host._guestStartingFresh || false)).toBe(false);
    await expectHarnessClean(page,errors);
  });
}
