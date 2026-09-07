const CONFIG_KEY = "exposed_entity_attributes";

const clonePreferences = (value = {}) => Object.fromEntries(
  Object.entries(value && typeof value === "object" && !Array.isArray(value) ? value : {})
    .filter(([reference, attributes]) => typeof reference === "string" && Array.isArray(attributes))
    .map(([reference, attributes]) => [reference, [...new Set(attributes.filter((name) => typeof name === "string" && name))].sort()]),
);

export function updateExposedAttributePreference(preferences, reference, attribute, enabled) {
  const result = clonePreferences(preferences);
  if (!reference || !attribute) return result;
  const selected = new Set(result[reference] || []);
  if (enabled) selected.add(attribute);
  else selected.delete(attribute);
  if (selected.size) result[reference] = [...selected].sort();
  else delete result[reference];
  return result;
}

export function removeExposedAttributePreference(preferences, reference) {
  const result = clonePreferences(preferences);
  delete result[reference];
  return result;
}

const selectedFor = (panel, reference, fallback = []) => {
  const configured = panel?._draft?.[CONFIG_KEY];
  return Array.isArray(configured?.[reference]) ? configured[reference] : fallback;
};

function attributeChoice(panel, entity, name, selected, missing = false) {
  const disabled = !entity.durable_selection_available;
  return `<label class="exposed-attribute-choice ${disabled ? "is-disabled" : ""}">
    <input type="checkbox" data-exposed-attribute data-reference="${panel._e(entity.reference || "")}" data-attribute="${panel._e(name)}" ${selected ? "checked" : ""} ${disabled ? "disabled" : ""}>
    <span><code>${panel._e(name)}</code>${missing ? `<small>Selected, but not present in the entity's current state</small>` : ""}</span>
  </label>`;
}

function entityAttributes(panel, entity) {
  const selected = new Set(selectedFor(panel, entity.reference, entity.selected_attributes || []));
  const available = Array.isArray(entity.attributes) ? entity.attributes : [];
  const missing = [...selected].filter((name) => !available.includes(name));
  const names = [...new Set([...available, ...missing])].sort();
  const selectionCount = selected.size;
  const identityNote = entity.durable_selection_available
    ? "Selections follow this Home Assistant entity across entity-ID renames. Values are read live only while the entity remains exposed to Assist."
    : "Home Assistant does not provide a stable entity-registry identity for this entity, so durable attribute selection is disabled to prevent preferences transferring if its entity ID is later reused.";
  return `<details class="exposed-attribute-entity" data-exposed-entity>
    <summary><span><strong>${panel._e(entity.name || entity.entity_id)}</strong><small><code>${panel._e(entity.entity_id)}</code></small></span><span class="exposed-attribute-count">${selectionCount ? `${selectionCount} selected` : "None selected"}</span></summary>
    <div class="exposed-attribute-body"><p class="help">${panel._e(identityNote)}</p>
      ${names.length ? `<div class="exposed-attribute-grid">${names.map((name) => attributeChoice(panel, entity, name, selected.has(name), missing.includes(name))).join("")}</div>` : `<p class="help">This entity currently reports no state attributes.</p>`}
    </div>
  </details>`;
}

function savedUnexposed(panel, item) {
  const selected = selectedFor(panel, item.reference, item.selected_attributes || []);
  return `<article class="exposed-saved-preference" data-saved-exposed-reference="${panel._e(item.reference)}"><div><strong>${panel._e(item.name || item.entity_id || "Unavailable entity")}</strong><small>${item.entity_id ? `<code>${panel._e(item.entity_id)}</code> · ` : ""}${panel._e(selected.join(", "))}</small><small>${item.registry_entry_exists ? "Saved preference is inactive because this entity is not currently exposed." : "The original registry entity no longer exists. The preference remains inert and will not transfer to a new entity that reuses its old entity ID."}</small></div><button type="button" class="secondary remove-exposed-preference" data-reference="${panel._e(item.reference)}">Remove saved preference</button></article>`;
}

export function renderExposedAttributeSettings(panel) {
  const catalog = panel?._result?.exposed_attribute_catalog || panel?._configData?.exposed_attribute_catalog || {};
  const entities = Array.isArray(catalog.entities) ? catalog.entities : [];
  const saved = Array.isArray(catalog.saved_unexposed) ? catalog.saved_unexposed : [];
  return `<div class="exposed-attribute-settings" data-setting data-search="exposed entity attributes additional state context brightness color temperature live values">
    <style>
      .exposed-attribute-settings{display:grid;gap:12px;padding:16px 0 4px;border-top:1px solid var(--divider-color)}
      .exposed-attribute-heading{display:flex;justify-content:space-between;gap:18px;align-items:start}.exposed-attribute-heading h3,.exposed-attribute-heading p{margin:0}.exposed-attribute-heading p{margin-top:5px;color:var(--secondary-text-color);line-height:1.45}
      .exposed-attribute-list{display:grid;border:1px solid var(--divider-color);border-radius:10px;overflow:hidden}.exposed-attribute-entity{margin:0;padding:0 14px;border:0;border-bottom:1px solid var(--divider-color)}.exposed-attribute-entity:last-child{border-bottom:0}.exposed-attribute-entity summary{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:58px}.exposed-attribute-entity summary>span:first-child{display:grid;gap:2px}.exposed-attribute-count{color:var(--secondary-text-color);font-size:12px;white-space:nowrap}.exposed-attribute-body{padding:0 0 14px}.exposed-attribute-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 14px}.exposed-attribute-choice{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;gap:9px;padding:7px 0;color:var(--primary-text-color)}.exposed-attribute-choice input{width:18px;min-height:18px;margin-top:1px}.exposed-attribute-choice span{display:grid;gap:2px;min-width:0}.exposed-attribute-choice code{overflow-wrap:anywhere}.exposed-attribute-choice.is-disabled{opacity:.55}
      .exposed-saved{display:grid;gap:8px;margin-top:4px}.exposed-saved h4{margin:0}.exposed-saved-preference{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px;border:1px solid var(--divider-color);border-radius:9px}.exposed-saved-preference>div{display:grid;gap:3px;min-width:0}.exposed-saved-preference small{overflow-wrap:anywhere}
      @media(max-width:680px){.exposed-attribute-grid{grid-template-columns:1fr}.exposed-saved-preference,.exposed-attribute-heading{display:grid}.exposed-saved-preference button{width:100%}}
    </style>
    <div class="exposed-attribute-heading"><div><h3>Additional entity attributes</h3><p>Select extra state attributes to include with exposed-device context. The configuration stores only stable entity references and attribute names; values are read live for each request.</p></div></div>
    ${entities.length ? `<div class="exposed-attribute-list">${entities.map((entity) => entityAttributes(panel, entity)).join("")}</div>` : `<p class="help">No entities are currently exposed to Assist.</p>`}
    ${saved.length ? `<section class="exposed-saved"><h4>Saved preferences not currently exposed</h4><p class="help">These preferences are retained for their original registry entities, but they cannot make an entity visible to the model.</p>${saved.map((item) => savedUnexposed(panel, item)).join("")}</section>` : ""}
    <span class="field-error" data-error="${CONFIG_KEY}"></span>
  </div>`;
}

function markConfigDirty(panel) {
  const existingControl = panel?.shadowRoot?.querySelector('[data-config="exposed_entities_enabled"]');
  if (existingControl && typeof Event !== "undefined") {
    existingControl.dispatchEvent(new Event("input"));
    return;
  }
  panel?._setConfigDirty?.(true);
}

function updateCount(input) {
  const entity = input.closest?.("[data-exposed-entity]");
  const count = entity?.querySelectorAll?.("[data-exposed-attribute]:checked")?.length || 0;
  const label = entity?.querySelector?.(".exposed-attribute-count");
  if (label) label.textContent = count ? `${count} selected` : "None selected";
}

export function bindExposedAttributeSettings(panel) {
  const root = panel?.shadowRoot;
  if (!root) return;
  root.querySelectorAll("[data-exposed-attribute]").forEach((input) => input.addEventListener("change", () => {
    panel._draft ||= {};
    panel._draft[CONFIG_KEY] = updateExposedAttributePreference(
      panel._draft[CONFIG_KEY], input.dataset.reference, input.dataset.attribute, input.checked,
    );
    updateCount(input);
    markConfigDirty(panel);
  }));
  root.querySelectorAll(".remove-exposed-preference").forEach((button) => button.addEventListener("click", () => {
    panel._draft ||= {};
    panel._draft[CONFIG_KEY] = removeExposedAttributePreference(panel._draft[CONFIG_KEY], button.dataset.reference);
    button.closest("[data-saved-exposed-reference]")?.remove();
    markConfigDirty(panel);
  }));
}
