import {expect, test} from "@playwright/test";
import {acceptConfirmation, fixtureUrl} from "./browser-helpers.mjs";

for (const bundle of [false, true]) for (const variant of ["unchanged", "newer edits", "baseline reversion", "blocked navigation", "failed refresh", "discard before refresh", "discard during refresh", "authentication"]) {
  test(`current configuration import retires only its submitted draft: ${variant} (${bundle ? "bundle" : "source"})`, async ({page}) => {
    await page.goto(fixtureUrl("assistant/basics", bundle ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    const tokens = panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await tokens.fill("777");
    await panel.evaluate(host => {
      const send = host.hass.callWS.bind(host.hass);
      window.importWrites = 0;
      window.followingConfigurationWrites = [];
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
        if (request.section === "configuration" && request.action === "save") followingConfigurationWrites.push(structuredClone(request));
        const result = await send(request);
        if (window.holdImportRead && request.section === "configuration" && request.action === "get") {
          await new Promise(resolve => {window.releaseImportRead = resolve;});
        }
        return result;
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
    if (variant.startsWith("discard ")) await panel.evaluate(host => {
      host.shadowRoot.querySelector("#import-dialog").close = () => {throw new Error("Presentation unavailable");};
    });
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
    if (variant.startsWith("discard ")) {
      if (variant === "discard during refresh") {
        await panel.evaluate(host => {
          window.holdImportRead = true;
          window.importRefresh = host._loadConfigDraft({preserveResult:true});
        });
        await expect.poll(() => page.evaluate(() => typeof window.releaseImportRead)).toBe("function");
      }
      await expect(tokens).toHaveValue("888");
      await panel.locator("#revert-config").click();
      await panel.locator('[data-config="__title"]').fill("Next deliberate title");
      if (variant === "discard during refresh") {
        await panel.evaluate(async host => {releaseImportRead(); await importRefresh; host._render();});
      } else await panel.locator("#save-config").click();
      // Refresh certifies the replacement baseline; it is not another mutation.
      await expect.poll(() => panel.evaluate(host => host._configDataStale)).toBe(false);
      expect(await page.evaluate(() => browserHarness.getState().configuration.title)).not.toBe("Next deliberate title");
      expect(await page.evaluate(() => followingConfigurationWrites)).toEqual([]);
      await panel.locator("#save-config").click();
      await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.title)).toBe("Next deliberate title");
      expect(await page.evaluate(() => browserHarness.getState().configuration.config.max_tokens)).toBe(1600);
      const writes = await page.evaluate(() => followingConfigurationWrites);
      expect(writes).toHaveLength(1);
      expect(writes[0].config).toEqual({});
      expect(writes[0].title).toBe("Next deliberate title");
      await page.goto(fixtureUrl("assistant/basics", bundle ? "&bundle=1" : ""));
      await expect(tokens).toHaveValue("1600");
      return;
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
