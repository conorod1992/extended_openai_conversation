import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

const localMetadata = {
  local_handling: {
    supported: true,
    intents: [{intent: "HassTurnOn", label: "Turn on"}],
    pipeline_conflicts: [],
  },
};

for (const bundled of [false, true]) {
  test(`an early warm-route hydration cannot replace current entity choices (${bundled ? "bundle" : "source"})`, async ({page}) => {
    await page.goto(fixtureUrl("assistant/prompt-context", bundled ? "&bundle=1" : ""));
    const panel = page.locator("extended-openai-management-panel");
    await expect(panel.locator(".exposed-attribute-settings")).toBeVisible();
    await expect(panel.locator("[data-exposed-feature]")).toHaveCount(0);
    await panel.evaluate(host => host._navigate("assistant", "basics"));
    await page.evaluate(() => {
      const host = document.querySelector("extended-openai-management-panel");
      delete host._configData.exposed_attribute_catalog;
      for (const snapshot of host._cleanConfigSnapshots.values()) delete snapshot.result.exposed_attribute_catalog;
      host._eocLiveMetadataCache.clear();
      window.exposureReads = [];
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      browserHarness.hass.callWS = message => message.action === "live_metadata" && message.metadata_keys.includes("exposed_attribute_catalog")
        ? new Promise(resolve => window.exposureReads.push(resolve)) : original(message);
    });
    await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
    await expect.poll(() => page.evaluate(() => window.exposureReads.length)).toBe(1);
    // Supersede the first hydration with a new authoritative visit/load while
    // the same cached placeholder is mounted, as native warm navigation can do.
    await panel.evaluate(host => {
      ++host._loadToken;
      void host._loadConfigurationLiveMetadata("assistant/prompt-context");
    });
    await expect.poll(() => page.evaluate(() => window.exposureReads.length)).toBe(2);
    const metadata = {exposed_attribute_catalog:{entities:[{
      entity_id:"light.current", name:"Current light", reference:"registry:current", attributes:["brightness"], durable_selection_available:true,
    }], saved_unexposed:[]}};
    await page.evaluate(result => window.exposureReads[0](result), metadata);
    await page.evaluate(() => new Promise(requestAnimationFrame));
    // Let the abandoned hydrate finish before the current read. It must leave
    // the placeholder owned by the active visit available for hydration.
    await expect(panel.getByText("Unable to load exposed entity attributes.", {exact:true})).toHaveCount(0);
    await expect(panel.locator("[data-exposed-feature]")).toHaveCount(1);
    await page.evaluate(result => window.exposureReads[1](result), metadata);
    await expect(panel.locator(".exposed-picker-wrap")).toBeVisible();
    expect(await panel.evaluate(host => host._result.exposed_attribute_catalog.entities[0].entity_id)).toBe("light.current");
  });
}

test("failed exposure refresh warns about cached entities and retries without losing edits", async ({page}) => {
  await page.goto(fixtureUrl("assistant/prompt-context"));
  const panel = page.locator("extended-openai-management-panel");
  await expect.poll(() => panel.evaluate(host => Boolean(host._result?.exposed_attribute_catalog))).toBe(true);
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await page.evaluate(() => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    window.failExposureRefresh = true;
    browserHarness.hass.callWS = message => {
      if (message.action === "live_metadata" && message.metadata_keys.includes("exposed_attribute_catalog")) {
        return window.failExposureRefresh ? Promise.reject(new Error("Exposure unavailable"))
          : Promise.resolve({exposed_attribute_catalog:{entities:[], saved_unexposed:[]}});
      }
      return original(message);
    };
  });
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.getByText("Unable to refresh Assist exposure.", {exact:true})).toBeVisible();
  await expect(panel.getByText("Showing the last known catalogue", {exact:false})).toBeVisible();
  await panel.locator("#prompt-editor").fill("Keep my unsaved prompt");
  await page.evaluate(() => { window.failExposureRefresh = false; });
  await panel.getByRole("button", {name:"Retry exposure refresh"}).click();
  await expect(panel.getByText("Unable to refresh Assist exposure.", {exact:true})).toHaveCount(0);
  await expect(panel.locator("#prompt-editor")).toHaveValue("Keep my unsaved prompt");
  expect(await panel.evaluate(host => host._result.exposed_attribute_catalog.entities)).toEqual([]);
});

test("resident Home Assistant configuration paints before live metadata and keeps edits", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    hass.callWS = message => message.section === "configuration" && message.action === "live_metadata"
      ? new Promise(resolve => { window.resolveLocalMetadata = resolve; }) : original(message);
  });

  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  const enabled = panel.locator('[data-config="local_intents_enabled"]');
  await expect(enabled).toBeVisible();
  expect(await panel.evaluate(host => host._result.local_handling)).toBeUndefined();
  await enabled.check();
  expect(await panel.evaluate(host => host._configDirty)).toBe(true);
  await page.evaluate(metadata => window.resolveLocalMetadata(metadata), localMetadata);
  await expect(panel.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toHaveCount(1);
  await expect(enabled).toBeChecked();
  expect(await panel.evaluate(host => host._configDirty)).toBe(true);
});

test("production bundle paints resident configuration before metadata completes", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics", "&bundle=1"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    hass.callWS = message => message.section === "configuration" && message.action === "live_metadata"
      ? new Promise(resolve => { window.resolveLocalMetadata = resolve; }) : original(message);
  });
  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  await expect(panel.locator('[data-config="local_intents_enabled"]')).toBeVisible();
  expect(await panel.evaluate(host => host._result.local_handling)).toBeUndefined();
  await page.evaluate(metadata => window.resolveLocalMetadata(metadata), localMetadata);
  await expect(panel.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toHaveCount(1);
});

test("same-revision live metadata survives unrelated navigation", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  await expect(panel.locator('[data-config="local_intents_enabled"]')).toBeVisible();
  await expect.poll(() => page.evaluate(() => browserHarness.calls.filter(
    call => call.section === "configuration" && call.action === "live_metadata",
  ).length)).toBe(1);
  await expect.poll(() => panel.evaluate(host => host._eocLiveMetadataCache?.has("local_handling"))).toBe(true);
  await panel.locator('.top-nav button[data-page="usage-maintenance"]').click();
  await expect(panel.locator("#usage-window")).toBeVisible();
  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  await expect(panel.locator('[data-config="local_intents_enabled"]')).toBeVisible();
  expect(await page.evaluate(() => browserHarness.calls.filter(
    call => call.section === "configuration" && call.action === "live_metadata",
  ).length)).toBe(1);
});

test("intent warming reuses one request when its route opens", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    window.metadataReads = [];
    hass.callWS = message => {
      if (message.section === "configuration" && message.action === "live_metadata") {
        metadataReads.push(message.metadata_keys);
        return new Promise(resolve => { window.resolveLocalMetadata = resolve; });
      }
      return original(message);
    };
  });
  await panel.evaluate(host => {
    const target = {dataset: {page: "capabilities", subsection: "home-assistant"}};
    host._warmNavigationTarget(target);
    host._warmNavigationTarget(target);
  });
  await expect.poll(() => page.evaluate(() => metadataReads.length)).toBe(1);
  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  await expect(panel.locator('[data-config="local_intents_enabled"]')).toBeVisible();
  expect(await page.evaluate(() => metadataReads)).toEqual([["local_handling"]]);
  await page.evaluate(metadata => window.resolveLocalMetadata(metadata), localMetadata);
  await expect(panel.locator('[data-local-intent-exclusion][value="HassTurnOn"]')).toHaveCount(1);
});

test("late metadata does not decorate another route or a cleared agent draft", async ({page}) => {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    window.liveMetadataReads = 0;
    hass.callWS = message => {
      if (message.section === "configuration" && message.action === "live_metadata") {
        window.liveMetadataReads++;
        return new Promise(resolve => { window.resolvePromptMetadata = resolve; });
      }
      return original(message);
    };
  });
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator("#prompt-editor")).toBeVisible();
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await page.evaluate(() => window.resolvePromptMetadata({exposed_attribute_catalog: {entities: [], saved_unexposed: []}}));
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  expect(await panel.evaluate(host => host._result?.exposed_attribute_catalog)).toBeUndefined();

  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect.poll(() => page.evaluate(() => window.liveMetadataReads)).toBe(2);
  expect(await panel.evaluate(host => host._result?.exposed_attribute_catalog)).toBeUndefined();
  // Returning must validate a fresh response; the abandoned visit cannot seed
  // the new editor or its metadata cache.
  await page.evaluate(() => window.resolvePromptMetadata({exposed_attribute_catalog: {entities: [], saved_unexposed: []}}));
  await expect.poll(() => panel.evaluate(host => Boolean(host._result?.exposed_attribute_catalog))).toBe(true);
  await panel.evaluate(host => host._navigate("capabilities", "home-assistant"));
  await expect(panel.locator('[data-config="local_intents_enabled"]')).toBeVisible();
  await panel.evaluate(host => {
    host._agentId = "another-agent";
    host._clearConfigDraft();
  });
  await page.evaluate(async metadata => {
    window.resolvePromptMetadata(metadata);
    await new Promise(resolve => setTimeout(resolve, 0));
  }, localMetadata);
  expect(await panel.evaluate(host => host._configData)).toBeNull();
});
