const targetValue = (rule) => {
  for (const kind of ["entity_id", "device_id", "area_id"]) {
    if (rule?.[kind]?.length) return {kind, value: rule[kind]};
  }
  return {kind: "", value: []};
};

export function renderProtectedActions(panel) {
  const result = panel._result || {}, rules = result.rules || [];
  return `<section class="page-intro"><div><h1>Protected Actions</h1><p>Add an extra check before Extended OpenAI performs sensitive or easy-to-trigger actions.</p></div><button type="button" id="protected-add">Add protection rule</button></section>
    <section class="content-card"><div class="section-heading"><div><h2>Local PIN</h2><p>PIN checking happens locally in Home Assistant. The PIN and your PIN replies are never sent to OpenAI.</p></div><strong class="status-value ${result.pin_configured ? "on" : ""}">${result.pin_configured ? "Set" : "Not set"}</strong></div><div class="section-actions"><button type="button" id="pin-set">${result.pin_configured ? "Change PIN" : "Set PIN"}</button>${result.pin_configured ? `<button type="button" class="danger secondary-danger" id="pin-remove">Remove PIN</button>` : ""}</div></section>
    <section class="notice"><strong>Confirmation is not authentication</strong><p>Ask for confirmation helps prevent accidents. Require PIN adds a locally verified secret. Guest Mode and normal permissions are always checked first and cannot be overridden by a PIN.</p></section>
    <section class="rule-list">${rules.map((rule) => `<article class="request-rule-card ${rule.enabled ? "" : "disabled"}"><div class="rule-card-heading"><div><span class="type-badge ${rule.protection === "pin" ? "local" : "routing"}">${rule.protection === "pin" ? "Require PIN" : "Ask for confirmation"}</span><h2>${panel._e(rule.name)}</h2></div><label class="switch-label"><span class="sr-only">Enable ${panel._e(rule.name)}</span><input class="protected-enabled" data-id="${panel._e(rule.id)}" type="checkbox" ${rule.enabled ? "checked" : ""}></label></div><p><code>${panel._e(`${rule.domain}.${rule.service}`)}</code>${[...(rule.entity_id || []), ...(rule.device_id || []), ...(rule.area_id || [])].length ? ` · ${panel._e([...(rule.entity_id || []), ...(rule.device_id || []), ...(rule.area_id || [])].join(", "))}` : " · Any target"}</p><div class="actions"><button type="button" class="secondary protected-edit" data-id="${panel._e(rule.id)}">Edit</button><button type="button" class="danger secondary-danger protected-delete" data-id="${panel._e(rule.id)}">Delete</button></div></article>`).join("") || `<section class="content-card empty-state"><h2>No protected actions yet</h2><p>For example, require a PIN for <code>lock.unlock</code>, or confirmation for <code>homeassistant.restart</code>.</p><button type="button" id="protected-empty-add">Add protection rule</button></section>`}</section>`;
}

export function protectedActionsDialogs() {
  return `<dialog id="protected-dialog" class="editor-dialog wide" aria-labelledby="protected-title"><form id="protected-form"><div class="dialog-header"><h2 id="protected-title">Add protection rule</h2><button type="button" class="icon protected-close" aria-label="Close">×</button></div><div class="dialog-body"><div class="form-grid"><label>Rule name<input id="protected-name" required maxlength="120" placeholder="Front door unlock"></label><label class="toggle"><span>Enabled</span><input id="protected-enabled-edit" type="checkbox" checked></label></div><label>Home Assistant action <span title="Choose the domain and action Extended OpenAI must protect." aria-label="Help: choose the domain and action Extended OpenAI must protect">ⓘ</span><input id="protected-service" list="protected-service-list" required placeholder="lock.unlock"></label><datalist id="protected-service-list"></datalist><label>Extra check<select id="protected-level"><option value="confirmation">Ask for confirmation</option><option value="pin">Require PIN</option></select><small>Confirmation prevents accidental triggers. PIN verifies the spoken or entered digits locally.</small></label><p id="protected-pin-guidance" class="notice" hidden>Set a PIN on the Protected Actions page before saving a PIN-protected rule.</p><label>Limit to a target (optional)<select id="protected-target-kind"><option value="">Any target</option><option value="entity_id">Entity</option><option value="device_id">Device</option><option value="area_id">Area</option></select></label><label id="protected-target-label" hidden>Target<ha-selector id="protected-target"></ha-selector><small>Leave this out to protect every use of the selected action.</small></label><div id="protected-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary protected-close">Cancel</button><button type="submit">Save rule</button></div></form></dialog>
    <dialog id="pin-dialog" class="editor-dialog" aria-labelledby="pin-title"><form id="pin-form"><div class="dialog-header"><h2 id="pin-title">Set PIN</h2><button type="button" class="icon pin-close" aria-label="Close">×</button></div><div class="dialog-body"><p>Your PIN is hashed and checked locally. It is not shown again or sent to an AI provider.</p><label>PIN<input id="pin-value" type="password" inputmode="numeric" autocomplete="new-password" minlength="4" maxlength="12" required></label><label>Repeat PIN<input id="pin-repeat" type="password" inputmode="numeric" autocomplete="new-password" minlength="4" maxlength="12" required></label><div id="pin-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary pin-close">Cancel</button><button type="submit">Save PIN</button></div></form></dialog>`;
}

export function bindProtectedActions(panel) {
  const root = panel.shadowRoot, q = (selector) => root.querySelector(selector);
  const result = panel._result || {}, rules = result.rules || [];
  const selector = q("#protected-target");
  const configureTarget = () => {
    const kind = q("#protected-target-kind").value;
    q("#protected-target-label").hidden = !kind;
    if (!kind) return;
    selector.hass = panel._hass;
    selector.selector = kind === "entity_id" ? {entity:{multiple:true}} : kind === "device_id" ? {device:{multiple:true}} : {area:{multiple:true}};
    selector.value = panel._protectedTarget || [];
  };
  const showPinGuidance = () => { q("#protected-pin-guidance").hidden = result.pin_configured || q("#protected-level").value !== "pin"; };
  const open = (id = null) => {
    const rule = rules.find((item) => item.id === id), target = targetValue(rule);
    panel._editingProtectedId = id; panel._protectedTarget = [...target.value];
    q("#protected-title").textContent = rule ? "Edit protection rule" : "Add protection rule";
    q("#protected-name").value = rule?.name || "";
    q("#protected-enabled-edit").checked = rule?.enabled ?? true;
    q("#protected-service").value = rule ? `${rule.domain}.${rule.service}` : "";
    q("#protected-level").value = rule?.protection || "confirmation";
    q("#protected-target-kind").value = target.kind;
    q("#protected-error").textContent = ""; configureTarget(); showPinGuidance(); q("#protected-dialog").showModal();
  };
  const populateServiceCatalog = (catalog) => { const entries = Object.entries(catalog || {}).flatMap(([domain, services]) => Object.entries(services).map(([service, description]) => ({domain,service,description}))); q("#protected-service-list")?.replaceChildren(...entries.map(({domain,service,description}) => { const option = document.createElement("option"); option.value = `${domain}.${service}`; option.label = description.name || option.value; return option; })); };
  const openWithServiceCatalog = (id = null) => {
    open(id);
    if (panel._serviceCatalog) return;
    const loadToken = (panel._protectedCatalogLoadToken || 0) + 1, dialog = q("#protected-dialog"), save = q('#protected-form button[type="submit"]'), error = q("#protected-error");
    panel._protectedCatalogLoadToken = loadToken;
    save.disabled = true;
    error.textContent = "Loading Home Assistant actions…";
    panel._loadServiceCatalog().then((catalog) => {
      if (panel._protectedCatalogLoadToken !== loadToken || !dialog.isConnected || !dialog.open) return;
      populateServiceCatalog(catalog);
      error.textContent = "";
      save.disabled = false;
    }, (err) => {
      if (panel._protectedCatalogLoadToken === loadToken && dialog.isConnected && dialog.open) error.textContent = `Unable to load Home Assistant actions: ${err.message || String(err)}`;
    });
  };
  populateServiceCatalog(panel._serviceCatalog);
  q("#protected-add")?.addEventListener("click", () => openWithServiceCatalog()); q("#protected-empty-add")?.addEventListener("click", () => openWithServiceCatalog());
  root.querySelectorAll(".protected-edit").forEach((button) => button.addEventListener("click", () => openWithServiceCatalog(button.dataset.id)));
  root.querySelectorAll(".protected-delete").forEach((button) => button.addEventListener("click", async () => { if (!await panel._confirm("Delete protection rule?", "The action will run normally after this rule is removed.", "Delete")) return; await panel._call("protected_actions", "delete", {rule_id:button.dataset.id,confirm:true}); await panel._loadSection(); }));
  root.querySelectorAll(".protected-enabled").forEach((input) => input.addEventListener("change", async () => { const rule = rules.find((item) => item.id === input.dataset.id); await panel._call("protected_actions", "update", {rule_id:rule.id,rule:{...rule,enabled:input.checked}}); await panel._loadSection(true); }));
  q("#protected-target-kind")?.addEventListener("change", () => { panel._protectedTarget = []; configureTarget(); }); selector?.addEventListener("value-changed", (event) => { panel._protectedTarget = event.detail.value || []; });
  q("#protected-level")?.addEventListener("change", showPinGuidance);
  root.querySelectorAll(".protected-close").forEach((button) => button.addEventListener("click", () => q("#protected-dialog").close()));
  q("#protected-form")?.addEventListener("submit", async (event) => { event.preventDefault(); try { const [domain, service] = q("#protected-service").value.trim().split(".", 2); if (!domain || !service) throw new Error("Choose an action in domain.action format."); if (q("#protected-level").value === "pin" && !result.pin_configured) throw new Error("Set a PIN before creating a PIN-protected rule."); const kind = q("#protected-target-kind").value, rule = {name:q("#protected-name").value,enabled:q("#protected-enabled-edit").checked,domain,service,protection:q("#protected-level").value,entity_id:kind === "entity_id" ? panel._protectedTarget : [],device_id:kind === "device_id" ? panel._protectedTarget : [],area_id:kind === "area_id" ? panel._protectedTarget : [],order:rules.find((item) => item.id === panel._editingProtectedId)?.order ?? rules.length}; await panel._call("protected_actions", panel._editingProtectedId ? "update" : "create", {...(panel._editingProtectedId ? {rule_id:panel._editingProtectedId} : {}),rule}); q("#protected-dialog").close(); await panel._loadSection(); } catch (err) { q("#protected-error").textContent = err.message || String(err); } });
  q("#pin-set")?.addEventListener("click", () => { q("#pin-title").textContent = result.pin_configured ? "Change PIN" : "Set PIN"; q("#pin-value").value = q("#pin-repeat").value = ""; q("#pin-error").textContent = ""; q("#pin-dialog").showModal(); });
  root.querySelectorAll(".pin-close").forEach((button) => button.addEventListener("click", () => q("#pin-dialog").close()));
  q("#pin-form")?.addEventListener("submit", async (event) => { event.preventDefault(); try { if (result.pin_configured && !await panel._confirm("Change local PIN?", "Pending PIN challenges will be cancelled.", "Change PIN")) return; await panel._call("protected_actions", "set_pin", {pin:q("#pin-value").value,pin_repeat:q("#pin-repeat").value}); q("#pin-dialog").close(); await panel._loadSection(); } catch (err) { q("#pin-error").textContent = err.message || String(err); } });
  q("#pin-remove")?.addEventListener("click", async () => { if (!await panel._confirm("Remove local PIN?", "Remove PIN-protected rules first. Confirmation-only rules are unaffected.", "Remove PIN")) return; try { await panel._call("protected_actions", "remove_pin", {confirm:true}); await panel._loadSection(); } catch (err) { panel._toast(err.message || String(err), true); } });
}
