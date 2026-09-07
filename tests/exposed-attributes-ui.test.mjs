import assert from "node:assert/strict";

import {
  removeExposedAttributePreference,
  renderExposedAttributeSettings,
  updateExposedAttributePreference,
} from "../custom_components/extended_openai_conversation_responses/frontend/exposed-attributes-ui.js";

{
  const original = {"registry:stable": ["color_temp"]};
  const updated = updateExposedAttributePreference(original, "registry:stable", "brightness", true);
  assert.deepEqual(updated, {"registry:stable": ["brightness", "color_temp"]});
  assert.deepEqual(original, {"registry:stable": ["color_temp"]}, "updates must not mutate the saved draft object");

  const removedOne = updateExposedAttributePreference(updated, "registry:stable", "color_temp", false);
  assert.deepEqual(removedOne, {"registry:stable": ["brightness"]});
  assert.deepEqual(
    updateExposedAttributePreference(removedOne, "registry:stable", "brightness", false),
    {},
    "empty selections should not leave inert empty records in config",
  );
}

{
  const preferences = {
    "registry:one": ["brightness"],
    "registry:two": ["temperature"],
  };
  assert.deepEqual(removeExposedAttributePreference(preferences, "registry:one"), {
    "registry:two": ["temperature"],
  });
  assert.deepEqual(preferences, {
    "registry:one": ["brightness"],
    "registry:two": ["temperature"],
  });
}

{
  const panel = {
    _e: (value) => String(value),
    _draft: {
      exposed_entity_attributes: {
        "registry:current": ["brightness", "missing"],
        "registry:inactive": ["color_temp"],
      },
    },
    _result: {
      exposed_attribute_catalog: {
        entities: [
          {
            entity_id: "light.kitchen",
            name: "Kitchen Lamp",
            reference: "registry:current",
            attributes: ["brightness", "color_mode"],
            selected_attributes: ["brightness", "missing"],
            missing_selected_attributes: ["missing"],
            durable_selection_available: true,
          },
          {
            entity_id: "sensor.registryless",
            name: "Registryless Sensor",
            reference: null,
            attributes: ["unit_of_measurement"],
            selected_attributes: [],
            missing_selected_attributes: [],
            durable_selection_available: false,
          },
        ],
        saved_unexposed: [
          {
            reference: "registry:inactive",
            entity_id: "light.hidden",
            name: "light.hidden",
            selected_attributes: ["color_temp"],
            registry_entry_exists: true,
          },
          {
            reference: "registry:deleted",
            entity_id: null,
            name: "Unavailable entity",
            selected_attributes: ["old_attribute"],
            registry_entry_exists: false,
          },
        ],
      },
    },
  };

  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /Additional entity attributes/);
  assert.match(html, /Kitchen Lamp/);
  assert.match(html, /brightness/);
  assert.match(html, /Selected, but not present in the entity's current state/);
  assert.match(html, /Registryless Sensor/);
  assert.match(html, /durable attribute selection is disabled/);
  assert.match(html, /Saved preferences not currently exposed/);
  assert.match(html, /cannot make an entity visible to the model/);
  assert.match(html, /will not transfer to a new entity that reuses its old entity ID/);

  const registrylessChoice = html.match(/<input type="checkbox" data-exposed-attribute data-reference="" data-attribute="unit_of_measurement"[^>]+>/)?.[0];
  assert.ok(registrylessChoice);
  assert.match(registrylessChoice, /disabled/);
}

{
  const panel = {
    _e: (value) => String(value),
    _draft: {},
    _result: {exposed_attribute_catalog: {entities: [], saved_unexposed: []}},
  };
  assert.match(renderExposedAttributeSettings(panel), /No entities are currently exposed to Assist/);
}
