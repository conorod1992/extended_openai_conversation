# Exposed entity attributes

Extended OpenAI can include selected Home Assistant state attributes alongside the normal exposed-device context. This is useful when an entity's main state is not enough for the model, such as a light's brightness or colour temperature.

## Exposure remains controlled by Home Assistant

This feature does **not** expose entities.

Home Assistant's Assist exposure settings remain the source of truth. Extended OpenAI first gets the entities currently exposed to Assist and allowed for the active Home Assistant caller, then adds configured attributes only to entities already in that result.

A saved attribute preference therefore cannot make a hidden entity visible. If an entity is no longer exposed, the preference becomes inactive. If the same entity is exposed again later, its saved preference can become active again.

## Stable entity identity

Durable preferences are stored against Home Assistant **entity-registry entry IDs**, not entity IDs such as `light.kitchen`.

This means:

- renaming an entity ID does not lose its selected attributes;
- deleting an entity makes its old preference inert;
- creating a different entity later with the deleted entity's old entity ID does not inherit the old preference;
- entities without a stable entity-registry entry can still appear in normal exposed-device context, but their attributes cannot be selected durably.

The saved agent configuration contains only the stable registry reference and selected attribute names. It never stores a copied attribute value.

## Live values

Selected values are resolved from Home Assistant when the effective prompt is rendered. Preview uses the same effective rendering path, so Preview shows current values and includes their rendered characters in its request-footprint count.

If a selected attribute temporarily disappears from an entity's state, Extended OpenAI simply omits that value for the request. The selection is retained and can become active again if Home Assistant reports the attribute later.

To keep prompt growth bounded:

- one serialized attribute value is limited to 4,096 characters;
- the combined selected-attribute payload is conservatively limited to 32,768 serialized characters per rendered request.

A single value over its limit is represented by a short omission marker. Additional values that would exceed the combined request limit are omitted from that render; the saved preference is unchanged.

## Configure attributes

Open the agent's **Prompt & context** settings and find **Additional entity attributes** below **Include exposed devices**.

The selector is built from entities currently exposed to Assist and the attribute names currently present in their Home Assistant states. Previously selected attributes that are temporarily missing remain visible as selected but unavailable.

Saved preferences for entities that are not currently exposed are shown separately. You can keep them for a future re-exposure or remove them explicitly.

The preferences are part of the normal agent configuration, so normal Save, duplicate, configuration import/export, full backup, and restore flows carry them with the rest of the agent settings.

## Prompt representation

With no selected live attributes, the maintained exposed-device representation is unchanged.

When selected values are available, the maintained representation adds a compact `attributes` column containing JSON for configured entities. Unconfigured entities do not gain attribute values.

Custom prompt or exposed-device templates receive the same enriched `exposed_entities` data. An entity has an `attributes` mapping only when one or more selected values are currently available for that request. For example:

```jinja
{% for entity in exposed_entities %}
  {{ entity.entity_id }}: {{ entity.attributes | default({}) }}
{% endfor %}
```

Custom templates do not bypass Assist exposure or caller permissions; they only format the already-filtered entity data supplied to the renderer.
