import {expect, test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

const cases = [
  {name:"complete", legacy:false},
  {name:"missing description", missing:"description"},
  {name:"missing title", missing:"title"},
  {name:"missing content", missing:"content"},
  {name:"invalid description", invalid:"description", value:{}},
  {name:"missing enabled", missing:"enabled"},
  {name:"invalid enabled", invalid:"enabled", value:"false"},
  {name:"legacy enabled omission", legacy:true},
];

for (const bundled of [false, true]) for (const scenario of cases) {
  test(`Knowledge read contract preserves the next stored edit: ${scenario.name} (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors = trackPageErrors(page);
    const panel = await openDataCollection(page, "knowledge", 1, bundled);
    const incomplete = Boolean(scenario.missing || scenario.invalid);
    await panel.evaluate(async (host, scenario) => {
      const stored = dataCollectionBackend.state.sources[0];
      stored.description = "Keep this independently saved description";
      stored.enabled = Boolean(scenario.legacy);
      window.knowledgeReadUpdateCalls = 0;
      window.knowledgeReadFault = true;
      const send = host.hass.callWS.bind(host.hass);
      host.hass.callWS = async request => {
        if (request.section === "knowledge" && request.action === "update") {
          knowledgeReadUpdateCalls++;
          // Independently enforce the production writer's revision policy.
          // The malformed read retains a valid revision, so that guard alone
          // cannot protect metadata silently defaulted by the editor.
          if (request.expected_revision !== stored.updated_at) throw {code:"invalid_request", message:"Source changed before save"};
        }
        const result = await send(request);
        if (request.section === "knowledge" && request.action === "list" && scenario.legacy) {
          result.sources.forEach(source => {delete source.enabled;});
        }
        if (request.section === "knowledge" && request.action === "get") {
          if (scenario.legacy) delete result.source.enabled;
          if (knowledgeReadFault && scenario.missing) delete result.source[scenario.missing];
          if (knowledgeReadFault && scenario.invalid) result.source[scenario.invalid] = scenario.value;
        }
        return result;
      };
      host._sectionCache.clear();
      await host._loadSection(true);
    }, scenario);
    await panel.locator(".source-edit-button").first().click();
    await expect(panel.locator("#knowledge-dialog-title")).toHaveText(/^(Edit Knowledge source|Unable to load source)$/);
    const editable = await panel.locator("#knowledge-content").isEnabled();
    if (editable) {
      await panel.locator("#knowledge-content").fill("Deliberately changed content only");
      await panel.locator("#knowledge-save").click();
    } else {
      // The form cannot authorize a write against a rejected read baseline.
      await panel.evaluate(host => host._saveKnowledge());
    }
    const stored = await page.evaluate(() => structuredClone(dataCollectionBackend.state.sources[0]));
    const writes = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "update"));
    expect(stored.description).toBe("Keep this independently saved description");
    expect(stored.enabled).toBe(Boolean(scenario.legacy));
    expect(writes).toHaveLength(incomplete ? 0 : 1);
    expect(await page.evaluate(() => knowledgeReadUpdateCalls)).toBe(incomplete ? 0 : 1);
    if (incomplete) {
      expect(stored.content).toBe("Reference text 0");
      await expect(panel.locator("#knowledge-save")).toBeDisabled();
      await panel.evaluate(async host => {
        knowledgeReadFault = false;
        host.shadowRoot.querySelector("#knowledge-dialog").close();
        await host._openKnowledge("source-0");
      });
      await expect(panel.locator("#knowledge-content")).toHaveValue("Reference text 0");
      await expect(panel.locator("#knowledge-source-enabled")).not.toBeChecked();
      await panel.locator("#knowledge-content").fill("Deliberate edit after authoritative reread");
      await panel.locator("#knowledge-save").click();
      await expect.poll(() => page.evaluate(() => dataCollectionBackend.state.sources[0].content)).toBe("Deliberate edit after authoritative reread");
      expect(await page.evaluate(() => knowledgeReadUpdateCalls)).toBe(1);
      expect(await page.evaluate(() => dataCollectionBackend.state.sources[0].description)).toBe("Keep this independently saved description");
      expect(await page.evaluate(() => dataCollectionBackend.state.sources[0].enabled)).toBe(false);
    } else {
      expect(stored.content).toBe("Deliberately changed content only");
      expect(writes[0].description).toBe("Keep this independently saved description");
      expect(writes[0].expected_revision).toBe("2026-09-01T12:00:00Z");
    }
    await expectHarnessClean(page, errors);
  });
}
