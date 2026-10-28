import {expect, test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

for (const bundled of [false, true]) for (const kind of ["knowledge", "persistent"]) {
  for (const failure of ["close", "cache", "render"]) {
    test(`${kind}: acknowledged create survives ${failure} failure (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const panel = await openDataCollection(page, kind, 1, bundled);
      const editor = kind === "knowledge" ? "knowledge" : "memory";
      await panel.locator(kind === "knowledge" ? "#add-source" : "#add-memory").click();
      if (kind === "knowledge") await panel.locator("#knowledge-title").fill("Committed source");
      await panel.locator(`#${editor}-content`).fill("Committed A");
      await panel.evaluate((host, {failure, editor}) => {
        const target = failure === "close" ? host.shadowRoot.querySelector(`#${editor}-dialog`) : host;
        const key = {close:"close", cache:"_invalidateAfterMutation", render:"_render"}[failure];
        const original = target[key];
        target[key] = () => { throw Error(`injected ${failure} failure`); };
        window.restoreAcknowledgementPresentation = () => { target[key] = original; };
      }, {failure, editor});
      await panel.locator(`#${editor}-save`).click();
      await expect(panel.locator("#toast")).toContainText(/(?:saved|added), but .*could not be refreshed/i);
      const state = () => page.evaluate(kind => ({
        records:kind === "knowledge" ? dataCollectionBackend.state.sources : dataCollectionBackend.state.memories,
        writes:dataCollectionBackend.calls.filter(call => ["create", "add", "update"].includes(call.action)),
      }), kind);
      expect((await state()).records).toHaveLength(2);
      expect((await state()).writes).toHaveLength(1);
      await page.evaluate(() => restoreAcknowledgementPresentation());
      if (failure === "close") {
        await panel.locator(`#${editor}-content`).fill("Newer B");
        await panel.locator(`#${editor}-save`).click();
        await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open", false);
        const saved = await state();
        expect(saved.records).toHaveLength(2);
        expect(saved.records.some(record => record.content === "Newer B")).toBe(true);
        expect(saved.writes.map(call => call.action)).toEqual([kind === "knowledge" ? "create" : "add", "update"]);
      }
      await expectHarnessClean(page, errors);
    });
  }

  test(`${kind}: incomplete reply remains uncertain after a real write (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, kind, 1, bundled);
    const editor = kind === "knowledge" ? "knowledge" : "memory";
    await panel.evaluate((host, kind) => {
      const original = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async request => {
        const result = await original(request);
        return request.section === (kind === "knowledge" ? "knowledge" : "memories") && request.action === (kind === "knowledge" ? "create" : "add") ? {} : result;
      };
    }, kind);
    await panel.locator(kind === "knowledge" ? "#add-source" : "#add-memory").click();
    if (kind === "knowledge") await panel.locator("#knowledge-title").fill("Uncertain source");
    await panel.locator(`#${editor}-content`).fill("Actually committed");
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("outcome is unknown");
    expect(await page.evaluate(kind => (kind === "knowledge" ? dataCollectionBackend.state.sources : dataCollectionBackend.state.memories).length, kind)).toBe(2);
    expect(await page.evaluate(() => dataCollectionBackend.calls.filter(call => ["create", "add"].includes(call.action)).length)).toBe(1);
    await expectHarnessClean(page, errors);
  });
}
