import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function openBasics(page) {
  await page.goto(fixtureUrl("assistant/basics"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Assistant settings", exact: true})).toBeVisible();
  return panel;
}

async function openMemories(page) {
  await page.goto(fixtureUrl("data-memory/memories"));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("heading", {name: "Memories", exact: true})).toBeVisible();
  return panel;
}

async function replaceAgentCatalogue(page, transform) {
  await page.evaluate((serialized) => {
    const transform = new Function("payload", `return (${serialized})(payload)`);
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    hass.callWS = async message => {
      const result = await original(message);
      if (
        message.type === "extended_openai_conversation_responses/management"
        && message.action === "agents"
        && !message.section
      ) {
        return transform(structuredClone(result));
      }
      return result;
    };
  }, transform.toString());
}

test("mounted frontend recovers from integration removal and clean re-add", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openMemories(page);
  await panel.locator("#add-memory").click();
  await panel.locator("#memory-content").fill("stale integration generation draft");

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    window.__integrationGeneration = "removed";
    hass.callWS = async message => {
      if (
        message.type === "extended_openai_conversation_responses/management"
        && message.action === "agents"
        && !message.section
      ) {
        if (window.__integrationGeneration === "removed") {
          throw new Error("Extended OpenAI config entry no longer exists");
        }
        const result = await original(message);
        return {
          ...result,
          agents: result.agents.map(agent => ({
            ...agent,
            entry_id: "entry-readded",
            subentry_id: "agent-readded",
            title: "Jarvis re-added",
          })),
        };
      }
      return original(message);
    };
  });

  await panel.evaluate(host => host._loadAgents());
  await expect(panel.locator(".route-error-state")).toBeVisible();
  await expect(panel.locator("#memory-dialog")).not.toBeVisible();

  await page.evaluate(() => {
    window.__integrationGeneration = "readded";
  });
  await panel.evaluate(host => host._loadAgents("agent-readded"));

  await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("agent-readded");
  await expect(panel.locator(".route-error-state")).toHaveCount(0);
  await expect(panel.getByText("Jarvis re-added", {exact: true})).toBeVisible();
  expect(
    await page.evaluate(() =>
      browserHarness.calls.filter(call => call.section === "memories" && call.action === "add").length,
    ),
  ).toBe(0);
  await expectHarnessClean(page, errors);
});

test("deleting the currently viewed HA user cannot leave its personal scope mounted", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openMemories(page);
  await expect.poll(() => panel.evaluate(host => host._scopeId)).toBe("user:test-user");
  await expect(panel.getByText("Baseline browser fixture memory", {exact: true})).toBeVisible();

  await replaceAgentCatalogue(page, payload => ({
    ...payload,
    scopes: [{
      scope_id: "user:replacement-user",
      scope_type: "user",
      display_name: "Replacement User",
      is_current_user: true,
      memory_count: 0,
      conversation_count: 0,
    }],
  }));
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    const original = hass.callWS.bind(hass);
    hass.callWS = async message => {
      const result = await original(message);
      if (
        message.type === "extended_openai_conversation_responses/management"
        && message.section === "scopes"
        && message.action === "catalog"
      ) {
        return {
          scopes: [{
            scope_id: "user:replacement-user",
            scope_type: "user",
            display_name: "Replacement User",
            is_current_user: true,
            memory_count: 0,
            conversation_count: 0,
          }],
        };
      }
      if (
        message.type === "extended_openai_conversation_responses/management"
        && message.section === "memories"
        && message.action === "list"
      ) {
        return {memories: [], total: 0};
      }
      return result;
    };
  });

  await panel.evaluate(host => host._loadAgents());
  await expect.poll(() => panel.evaluate(host => host._scopeId)).toBe("user:replacement-user");
  await expect(panel.getByText("Baseline browser fixture memory", {exact: true})).toHaveCount(0);
  await expect(panel.getByText("Replacement User", {exact: true})).toBeVisible();
  await expectHarnessClean(page, errors);
});

test("deleted and recreated subentry does not inherit a mounted dirty editor", async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openBasics(page);
  const title = panel.locator('[data-config="__title"]');
  await title.fill("unsaved deleted-subentry title");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toBeVisible();

  await replaceAgentCatalogue(page, payload => ({
    ...payload,
    agents: payload.agents.map(agent => ({
      ...agent,
      entry_id: "entry-1",
      subentry_id: "agent-recreated",
      title: "Recreated assistant",
    })),
  }));

  await panel.evaluate(host => host._loadAgents("agent-recreated"));
  await expect.poll(() => panel.evaluate(host => host._agentId)).toBe("agent-recreated");
  await expect(title).not.toHaveValue("unsaved deleted-subentry title");
  await expect(panel.getByText("Unsaved changes", {exact: true})).toHaveCount(0);

  const staleWrites = await page.evaluate(() =>
    browserHarness.calls.filter(
      call => call.section === "configuration"
        && ["update", "save"].includes(call.action)
        && call.title === "unsaved deleted-subentry title",
    ),
  );
  expect(staleWrites).toEqual([]);
  await expectHarnessClean(page, errors);
});

test("mounted native action selector keeps draft while saved service disappears", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".request-rule-card").first().locator(".rule-edit").click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);

  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  await expect(selector).toBeAttached();
  const savedAction = [{
    action: "light.turn_on",
    target: {entity_id: "light.lifecycle_target"},
    data: {brightness_pct: 37},
  }];

  await selector.evaluate((element, value) => {
    element.value = structuredClone(value);
    element.dispatchEvent(new CustomEvent("value-changed", {
      detail: {value: structuredClone(value)},
      bubbles: true,
      composed: true,
    }));
  }, savedAction);

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.services = {light: {turn_on: {name: "Turn on"}}};
    browserHarness.panel.hass = hass;
  });
  await expect(selector).toBeAttached();

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.services = {light: {}};
    browserHarness.panel.hass = hass;
  });

  await expect(selector).toBeAttached();
  expect(await selector.evaluate(element => element.value)).toEqual(savedAction);
  expect(await selector.evaluate(element => element.hass.services.light.turn_on)).toBeUndefined();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expectHarnessClean(page, errors);
});

test("mounted native selector receives entity device and area renames without losing its draft", async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules"));
  const panel = page.locator("extended-openai-management-panel");
  await panel.locator(".request-rule-card").first().locator(".rule-edit").click();
  const selector = panel.locator("#rule-action-sequence-host ha-selector");
  await expect(selector).toBeAttached();

  const draft = [{
    action: "light.turn_on",
    target: {
      entity_id: "light.rename_target",
      device_id: "device-lifecycle",
      area_id: "area-lifecycle",
    },
  }];
  await selector.evaluate((element, value) => {
    element.value = structuredClone(value);
  }, draft);

  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.states = {
      "light.rename_target": {
        entity_id: "light.rename_target",
        state: "off",
        attributes: {friendly_name: "Original entity name"},
      },
    };
    hass.entities = {
      "light.rename_target": {name: "Original entity name", device_id: "device-lifecycle", area_id: "area-lifecycle"},
    };
    hass.devices = {"device-lifecycle": {id: "device-lifecycle", name: "Original device name", area_id: "area-lifecycle"}};
    hass.areas = {"area-lifecycle": {area_id: "area-lifecycle", name: "Original area name"}};
    browserHarness.panel.hass = hass;
  });

  await selector.evaluate(element => {
    window.__mountedLifecycleSelector = element;
  });
  await page.evaluate(() => {
    const hass = browserHarness.hass;
    hass.states["light.rename_target"] = {
      entity_id: "light.rename_target",
      state: "off",
      attributes: {friendly_name: "Renamed entity"},
    };
    hass.entities["light.rename_target"] = {name: "Renamed entity", device_id: "device-lifecycle", area_id: "area-lifecycle"};
    hass.devices["device-lifecycle"] = {id: "device-lifecycle", name: "Renamed device", area_id: "area-lifecycle"};
    hass.areas["area-lifecycle"] = {area_id: "area-lifecycle", name: "Renamed area"};
    browserHarness.panel.hass = hass;
  });

  await expect(selector).toBeAttached();
  expect(await selector.evaluate(element => element.value)).toEqual(draft);
  expect(await selector.evaluate(element => ({
    entity: element.hass.states["light.rename_target"].attributes.friendly_name,
    device: element.hass.devices["device-lifecycle"].name,
    area: element.hass.areas["area-lifecycle"].name,
  }))).toEqual({
    entity: "Renamed entity",
    device: "Renamed device",
    area: "Renamed area",
  });
  expect(await selector.evaluate(element => element === window.__mountedLifecycleSelector)).toBe(true);
  await expectHarnessClean(page, errors);
});
