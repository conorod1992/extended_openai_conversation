import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl} from "./browser-helpers.mjs";

for (const bundle of [false, true]) for (const variant of ["unchanged", "newer edits", "baseline reversion", "blocked navigation", "failed refresh", "authentication"]) {
  test(`current configuration import retires only its submitted draft: ${variant} (${bundle ? "bundle" : "source"})`, async ({page}) => {
    await page.goto(fixtureUrl("assistant/basics", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    const tokens = panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await tokens.fill("777");
    await panel.evaluate(host => {
      const send = host.hass.callWS.bind(host.hass);
      window.importWrites = 0;
      host.hass.callWS = async request => {
        if (window.failImportRead && request.section === "configuration" && request.action === "get") throw {code:"offline", message:"Import refresh unavailable"};
        if (request.section === "configuration" && request.action === "import_preview") return {
          title:"Imported", summary:{model:"gpt-5-mini", tools:1, function_groups:1, speech_rules:0}};
        if (request.section === "configuration" && request.action === "import") {
          importWrites++;
          // The stateful backend owns the write and revision. Only the import
          // transport response is adapted; the draft owner remains production UI.
          await send({...request, action:"save", config:{max_tokens:1600}});
          await new Promise(resolve => {window.releaseConfigurationImport = resolve;});
          return {status:"updated", subentry_id:request.subentry_id};
        }
        return send(request);
      };
    });
    await panel.locator(".agent-actions-menu > summary").click();
    await panel.locator("#import-agent").click();
    await panel.locator('input[name="import-mode"][value="current"]').check();
    await panel.locator("#import-document").fill("reviewed configuration");
    await panel.locator("#import-preview").click();
    await panel.locator("#import-apply").click();
    await acceptConfirmation(panel);
    await expect.poll(() => page.evaluate(() => typeof window.releaseConfigurationImport)).toBe("function");
    await panel.locator("#import-cancel").click();
    if (variant === "blocked navigation") {
      await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
      expect(await panel.evaluate(host => host._viewKey())).toBe("assistant/basics");
    }
    if (variant !== "unchanged") await tokens.fill(variant === "baseline reversion" ? "1200" : "888");
    if (variant === "failed refresh") await page.evaluate(() => {window.failImportRead = true;});
    if (variant === "authentication") await panel.evaluate(host => {host.hass = {...host.hass, user:{id:"another-user"}};});
    await page.evaluate(() => releaseConfigurationImport());
    await expect.poll(() => panel.evaluate(host => host._eocImportPending)).toBe(false);
    if (variant === "blocked navigation") {
      await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
      await panel.locator('[data-config="prompt"]').fill("Authored after import acknowledgement");
      await panel.evaluate(host => host._navigate("assistant", "basics"));
    }
    if (variant === "failed refresh") {
      expect(await panel.evaluate(host => host._draft.max_tokens)).toBe(888);
      await panel.evaluate(async host => {window.failImportRead = false; await host._loadSection(true);});
    }
    const expected = variant === "unchanged" ? "1600" : variant === "baseline reversion" ? "1200" : "888";
    await expect(tokens).toHaveValue(expected);
    expect(await page.evaluate(() => importWrites)).toBe(1);
    if (variant === "authentication") {
      expect(await page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(1600);
      return;
    }
    if (variant !== "unchanged") {
      await panel.locator("#save-config").click();
      await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(Number(expected));
      if (variant === "blocked navigation") await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.prompt)).toBe("Authored after import acknowledgement");
    }
    await page.goto(fixtureUrl("assistant/basics", bundle ? "&bundle=1" : ""));
    await expect(tokens).toHaveValue(expected);
  });
}
