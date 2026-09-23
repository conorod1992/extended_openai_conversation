import {expect, test} from "@playwright/test";
import {fixtureUrl} from "./browser-helpers.mjs";

const localMetadata = {
  local_handling: {
    supported: true,
    intents: [{intent: "HassTurnOn", label: "Turn on"}],
    pipeline_conflicts: [],
  },
};

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
    hass.callWS = message => message.section === "configuration" && message.action === "live_metadata"
      ? new Promise(resolve => { window.resolvePromptMetadata = resolve; }) : original(message);
  });
  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
  await expect(panel.locator("#prompt-editor")).toBeVisible();
  await panel.evaluate(host => host._navigate("assistant", "basics"));
  await page.evaluate(() => window.resolvePromptMetadata({exposed_attribute_catalog: {entities: [], saved_unexposed: []}}));
  await expect(panel.locator('[data-config="__title"]')).toBeVisible();
  expect(await panel.evaluate(host => host._result?.exposed_attribute_catalog)).toBeUndefined();

  await panel.evaluate(host => host._navigate("assistant", "prompt-context"));
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
