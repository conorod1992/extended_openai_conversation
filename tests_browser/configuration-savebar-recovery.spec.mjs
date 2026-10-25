import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const routes = [
  {name:"Basics", page:"assistant", section:"basics", field:"__title", original:"Jarvis", submitted:"Submitted title A"},
  {name:"Voice", page:"assistant", section:"voice", field:"voice_scope_policy", original:"unretained", submitted:"shared"},
  {name:"Retention", page:"usage-maintenance", section:"retention", field:"usage_request_retention_days", original:"30", submitted:"7"},
];

for (const bundled of [false, true]) for (const route of routes) {
  test(`${route.name} restores Save and Discard after a reverted pending save and failed refresh (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="__title"]')).toHaveValue("Jarvis");
    await panel.evaluate(async (host, voice) => {
      host.hass = {...host.hass, user:{id:"admin", is_admin:true}};
      const original = host.hass.callWS.bind(host.hass);
      if (voice) {
        const agent = host._selectedAgent(), saved = browserHarness.getState().configuration;
        await original({type:"extended_openai_conversation_responses/management", section:"configuration", action:"save",
          entry_id:agent.entry_id, subentry_id:agent.subentry_id, revision:saved.revision,
          config:{voice_scope_policy:"unretained", voice_unmapped_policy:"unretained"}});
      }
      window.savebarRecovery = {writes:[], entered:false, failedReads:0};
      host.hass.callWS = async request => {
        if (request.section === "configuration" && request.action === "save") {
          window.savebarRecovery.writes.push(structuredClone(request));
          if (window.savebarRecovery.writes.length === 1) {
            window.savebarRecovery.entered = true;
            await new Promise(resolve => {window.savebarRecovery.releaseAcknowledgement = resolve;});
          }
        }
        if (request.section === "configuration" && request.action === "get" && window.savebarRecovery.failRead) {
          window.savebarRecovery.failRead = false;
          window.savebarRecovery.failedReads++;
          throw new Error("Generated configuration save refresh failure");
        }
        const result = await original(request);
        if (voice && request.section === "configuration" && request.action === "get") {
          // Supply the catalogue choices omitted by this small fixture's base
          // options, while retaining its actual revision and persistence.
          return {...result, options:{...result.options, voice_scope_policy:["unretained", "shared"], voice_unmapped_policy:["unretained", "shared"]}};
        }
        return result;
      };
      if (voice) host._invalidateCleanConfiguration(host._agentId);
    }, route.name === "Voice");
    if (route.section !== "basics") await panel.evaluate((host, route) => host._navigate(route.page, route.section), route);
    const control = panel.locator(`[data-config="${route.field}"]`);
    await expect(control).toHaveValue(route.original);
    if (route.name === "Basics") await control.fill(route.submitted);
    else await control.selectOption(route.submitted);
    await panel.locator("#save-config").click();
    await expect.poll(() => page.evaluate(() => window.savebarRecovery.entered)).toBe(true);
    if (route.name === "Basics") await control.fill(route.original);
    else await control.selectOption(route.original);
    // The original value is clean against O while A is still in flight, so
    // ordinary input handling removes both controls before the acknowledgement.
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    await panel.evaluate(async host => {
      host._invalidateCleanConfiguration(host._agentId);
      await host._loadSection(true);
    });
    if (route.name === "Basics") {
      await panel.locator('[data-config="max_tokens"]').focus();
      await panel.evaluate(host => {window.savebarRecovery.focusControl = host.shadowRoot.activeElement;});
    }
    await page.evaluate(() => {
      window.savebarRecovery.failRead = true;
      window.savebarRecovery.releaseAcknowledgement();
    });
    await expect.poll(() => panel.evaluate(host => host._configurationSaving)).toBe(false);
    expect(await page.evaluate(() => window.savebarRecovery.failedReads)).toBe(1);
    expect(await panel.evaluate(host => host._configDirty)).toBe(true);
    await expect(panel.locator("#save-config")).toBeVisible();
    await expect(panel.locator("#save-config")).toBeEnabled();
    await expect(panel.locator("#save-config")).toHaveText("Save changes");
    await expect(panel.locator("#revert-config")).toBeVisible();
    await expect(panel.locator("#revert-config")).toBeEnabled();
    await expect(control).toHaveValue(route.original);
    if (route.name === "Basics") expect(await panel.evaluate(host => host.shadowRoot.activeElement === window.savebarRecovery.focusControl
      && host.shadowRoot.querySelector('[data-config="max_tokens"]') === window.savebarRecovery.focusControl)).toBe(true);
    expect(await page.evaluate(() => window.savebarRecovery.writes.length)).toBe(1);

    // Save after a failed refresh first refreshes for review without replaying
    // the acknowledged write. The restored control remains usable afterwards.
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configDataStale)).toBe(false);
    expect(await page.evaluate(() => window.savebarRecovery.writes.length)).toBe(1);
    await expect(panel.locator("#save-config")).toBeEnabled();
    await expect(panel.locator("#revert-config")).toBeEnabled();
    await expectHarnessClean(page, diagnostics);
  });
}

for (const bundled of [false, true]) {
  test(`a clean configuration acknowledgement leaves no extra save bar (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const diagnostics = trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await panel.locator('[data-config="max_tokens"]').fill("1400");
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => host._configurationSaving)).toBe(false);
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    await expect(panel.locator(".save-bar")).toHaveCount(0);
    expect(await page.evaluate(() => browserHarness.calls.filter(request => request.section === "configuration" && request.action === "save").length)).toBe(1);
    await expectHarnessClean(page, diagnostics);
  });
}
