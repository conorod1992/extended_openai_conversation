import assert from "node:assert/strict";
import test from "node:test";

import {
  bindExposedAttributeSettings,
  removeExposedAttributePreference,
  renderExposedAttributeSettings,
  updateExposedAttributePreference,
} from "../custom_components/extended_openai_conversation_responses/frontend/exposed-attributes-ui.js";

for (const layout of ["legacy-combo", "mwc-list-item", "role-listitem", "modern", "modern-combo", "native-role"]) test(`exposed entity picker preserves ${layout} native semantics`, async () => {
  const legacy = ["legacy-combo", "mwc-list-item", "role-listitem"].includes(layout);
  const combo = {updateComplete:Promise.resolve(), shadowRoot:{querySelector:selector => layout === "legacy-combo" && selector.includes("li") ? {} : null}};
  const attributes = new Map(layout === "native-role" ? [["role", "group"], ["aria-label", "Native chooser"]] : []);
  const field = {
    updateComplete:Promise.resolve(),
    shadowRoot:{querySelector:selector => selector === "ha-combo-box-item" && layout.endsWith("-combo") ? combo
      : selector.split(",").map(part => part.trim()).includes(layout === "role-listitem" ? '[role="listitem"]' : layout) ? {} : null},
    setAttribute:(key, value) => attributes.set(key, value),
  };
  const generic = {updateComplete:Promise.resolve(), shadowRoot:{querySelector:() => field}};
  const picker = {isConnected:true, updateComplete:Promise.resolve(), shadowRoot:{querySelector:() => generic}, addEventListener() {}};
  const fallback = {addEventListener() {}};
  const panel = basePanel();
  panel._hass = {marker:"native hass"};
  panel.shadowRoot = {
    querySelector:selector => ({"#exposed-entity-picker":picker, "#exposed-entity-picker-fallback":fallback})[selector] || null,
    querySelectorAll:() => [],
  };
  const previous = Object.getOwnPropertyDescriptor(globalThis, "customElements");
  Object.defineProperty(globalThis, "customElements", {configurable:true, value:{get:() => true}});
  try {
    bindExposedAttributeSettings(panel);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(picker.hidden, false);
    assert.equal(fallback.hidden, true);
    assert.equal(picker.hass, panel._hass);
    assert.deepEqual(picker.includeEntities, ["light.kitchen", "sensor.registryless"]);
    assert.deepEqual([...attributes], legacy ? [["role", "list"], ["aria-label", "Entity selection"]]
      : layout === "native-role" ? [["role", "group"], ["aria-label", "Native chooser"]] : []);
  } finally {
    if (previous) Object.defineProperty(globalThis, "customElements", previous);
    else delete globalThis.customElements;
  }
});

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

const basePanel = () => ({
  _e: (value) => String(value),
  _draft: {
    exposed_entities_enabled: true,
    exposed_entity_attributes: {
      "registry:current": ["brightness", "missing"],
      "registry:inactive": ["color_temp"],
      "registry:deleted": ["old_attribute"],
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
});

{
  const html = renderExposedAttributeSettings(basePanel());
  assert.match(html, /Additional entity attributes/);
  assert.match(html, /ha-entity-picker/);
  assert.match(html, /Only entities currently exposed to Assist are available here/);
  assert.match(html, /Kitchen Lamp/);
  assert.match(html, /Registryless Sensor/);
  assert.match(html, /Configured entities/);
  assert.match(html, /brightness, missing/);
  assert.match(html, /Saved preferences not currently exposed/);
  assert.match(html, /cannot make an entity visible to the model/);
  assert.match(html, /will not transfer to a new entity that reuses its old entity ID/);
  assert.doesNotMatch(html, /Inactive while automatic entity context is off/);
}

{
  const panel = basePanel();
  panel._result.function_repair = {
    required: true,
    isolatable: true,
    invalid_count: 1,
  };
  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /Kitchen Lamp/);
  assert.doesNotMatch(html, /Unable to load exposed entity attributes/);
}

{
  const panel = basePanel();
  panel._exposedAttributeEntityId = "light.kitchen";
  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /data-exposed-editor/);
  assert.match(html, /data-attribute="brightness"/);
  assert.match(html, /Selected, but not present in the entity's current state/);
}

{
  const panel = basePanel();
  panel._exposedAttributeEntityId = "sensor.registryless";
  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /durable attribute selection is disabled/);
  const registrylessChoice = html.match(/<input type="checkbox" data-exposed-attribute data-reference="" data-attribute="unit_of_measurement"[^>]+>/)?.[0];
  assert.ok(registrylessChoice);
  assert.match(registrylessChoice, /disabled/);
}

{
  const panel = basePanel();
  panel._draft.exposed_entities_enabled = false;
  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /Inactive while automatic entity context is off/);
  assert.match(html, /selections are preserved and can still be edited here/i);
  assert.match(html, /ha-entity-picker/);
  assert.match(html, /Configured entities/);
  assert.match(html, /brightness, missing/);
}

{
  const panel = {
    _e: (value) => String(value),
    _draft: {exposed_entities_enabled: true},
    _result: {exposed_attribute_catalog: {entities: [], saved_unexposed: []}},
  };
  assert.match(renderExposedAttributeSettings(panel), /No entities are currently exposed to Assist/);
}

{
  const panel = {
    _e: (value) => String(value),
    _draft: {exposed_entities_enabled: true},
    _result: {},
  };
  const html = renderExposedAttributeSettings(panel);
  assert.match(html, /Unable to load exposed entity attributes/);
  assert.doesNotMatch(html, /No entities are currently exposed to Assist/);
}

{
  const panel=basePanel();
  panel._exposedAttributeEntityId="light.kitchen";
  panel._draft.exposed_entity_attributes={};
  const html=renderExposedAttributeSettings(panel);
  const choice=html.match(/<input[^>]*data-attribute="brightness"[^>]*>/)?.[0];
  assert.ok(choice);
  assert.doesNotMatch(choice,/checked/);
  assert.doesNotMatch(html,/data-exposed-configured-reference="registry:current"/);
}


{
  const panel = basePanel();
  panel._draft.exposed_entity_attributes = {};
  const html = renderExposedAttributeSettings(panel);
  assert.doesNotMatch(html, /data-saved-exposed-reference=/, "authoritative empty draft must hide saved inactive/deleted preferences");
  assert.doesNotMatch(html, /Saved preferences not currently exposed/);
  delete panel._draft.exposed_entity_attributes;
  assert.match(renderExposedAttributeSettings(panel), /data-saved-exposed-reference=/, "uninitialized draft may use the saved catalogue");
}
