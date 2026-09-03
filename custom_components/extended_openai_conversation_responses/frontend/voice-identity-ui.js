const SHARED_SCOPE = "shared:household";
const UNRETAINED_SCOPE = "unretained";

const VOICE_POLICY_LABELS = Object.freeze({
  unretained: "Do not retain personal data",
  shared: "Use shared household data",
  default_user: "Use the default user",
  device_mapping: "Use a device assignment",
});

const VOICE_FALLBACK_LABELS = Object.freeze({
  ...VOICE_POLICY_LABELS,
  device_mapping: "Device mapping again (no retained data)",
});

const VOICE_STYLE = `
  <style data-eoc-voice-identity>
    .voice-identity-intro{display:grid;gap:12px;margin-bottom:18px}
    .voice-identity-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .voice-flow-step{padding:12px 14px;border:1px solid var(--divider-color);border-radius:10px;background:var(--secondary-background-color)}
    .voice-flow-step strong{display:block;margin-bottom:4px}
    .voice-flow-step small{display:block;color:var(--secondary-text-color);line-height:1.4}
    .voice-policy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}
    .voice-policy-card,.voice-default-card,.voice-mappings-card{padding:16px;border:1px solid var(--divider-color);border-radius:12px;background:var(--card-background-color)}
    .voice-policy-card .setting,.voice-default-card .setting{margin:0}
    .voice-policy-card h3,.voice-default-card h3,.voice-mappings-card h3{margin:0 0 5px;font-size:15px}
    .voice-policy-card>p,.voice-default-card>p,.voice-mappings-card .section-heading p{margin:0 0 12px;color:var(--secondary-text-color)}
    .voice-default-card{margin-bottom:14px}
    .voice-mappings-card.is-disabled,.voice-policy-card.is-disabled,.voice-default-card.is-disabled{opacity:.72}
    .voice-mapping-list{display:grid;gap:10px;margin-top:12px}
    .voice-mapping-row{display:grid;grid-template-columns:minmax(160px,1fr) minmax(220px,1.2fr) auto;gap:10px;align-items:end;padding:12px;border:1px solid var(--divider-color);border-radius:10px;background:var(--primary-background-color)}
    .voice-mapping-row label{display:grid;gap:5px;font-size:13px;color:var(--secondary-text-color)}
    .voice-mapping-row input,.voice-mapping-row select{width:100%;box-sizing:border-box}
    .voice-mapping-empty{padding:14px;border:1px dashed var(--divider-color);border-radius:10px;color:var(--secondary-text-color)}
    .voice-current-summary{margin:0}
    @media (max-width:800px){
      .voice-identity-flow,.voice-policy-grid{grid-template-columns:1fr}
      .voice-mapping-row{grid-template-columns:1fr}
      .voice-mapping-row .remove-voice-mapping{justify-self:start}
    }
  </style>`;

function escapeValue(panel, value) {
  return panel._e(String(value ?? ""));
}

function rawUserId(scopeId) {
  const value = String(scopeId || "");
  return value.startsWith("user:") ? value.slice(5) : value;
}

export function voiceUsers(panel) {
  const scopes = panel?._baseScopes?.length ? panel._baseScopes : panel?._data?.scopes || [];
  const seen = new Set();
  return scopes
    .filter((scope) => scope?.scope_type === "user")
    .map((scope) => ({id:rawUserId(scope.scope_id), name:String(scope.display_name || rawUserId(scope.scope_id))}))
    .filter((user) => user.id && !seen.has(user.id) && seen.add(user.id));
}

export function voiceUserLabel(panel, userId) {
  if (!userId) return null;
  const normalized = rawUserId(userId);
  return voiceUsers(panel).find((user) => user.id === normalized)?.name || null;
}

function policyLabel(value, fallback = false) {
  const labels = fallback ? VOICE_FALLBACK_LABELS : VOICE_POLICY_LABELS;
  return labels[value] || String(value || "").replaceAll("_", " ");
}

function policyOptions(panel, key, selected, fallback = false) {
  const configured = panel?._result?.options?.[key] || [];
  const values = configured.map((item) => typeof item === "string" ? item : item.value);
  if (selected && !values.includes(selected)) values.push(selected);
  return values.map((value) => `<option value="${escapeValue(panel,value)}" ${value === selected ? "selected" : ""}>${escapeValue(panel,policyLabel(value,fallback))}</option>`).join("");
}

function policySelect(panel, key, label, selected, description, fallback = false) {
  return `<div class="setting" data-field="${key}" data-setting><label for="config-${key}">${escapeValue(panel,label)}</label><select id="config-${key}" data-config="${key}">${policyOptions(panel,key,selected,fallback)}</select><small>${escapeValue(panel,description)}</small><span class="field-error" data-error="${key}"></span></div>`;
}

function defaultUserOptions(panel, selected) {
  const users = voiceUsers(panel);
  const found = users.some((user) => user.id === selected);
  const unavailable = selected && !found
    ? `<option value="${escapeValue(panel,selected)}" selected>Unavailable user · ${escapeValue(panel,selected)}</option>`
    : "";
  return `<option value="" ${selected ? "" : "selected"}>Not selected</option>${unavailable}${users.map((user) => `<option value="${escapeValue(panel,user.id)}" ${user.id === selected ? "selected" : ""}>${escapeValue(panel,user.name)}</option>`).join("")}`;
}

function normalizeOwner(owner) {
  const value = String(owner || "");
  if (value === "shared" || value === SHARED_SCOPE) return {type:"shared", id:null};
  if (value === UNRETAINED_SCOPE) return {type:"unretained", id:null};
  const id = rawUserId(value);
  return {type:"user", id};
}

function ownerLabel(panel, owner) {
  const normalized = normalizeOwner(owner);
  if (normalized.type === "shared") return "Shared household";
  if (normalized.type === "unretained") return "No retained personal data";
  return voiceUserLabel(panel, normalized.id) || `Unavailable user · ${owner}`;
}

function ownerOptions(panel, selected) {
  const selectedNormalized = normalizeOwner(selected);
  const values = [];
  const push = (value, label, identity) => {
    if (values.some((item) => item.identity === identity)) return;
    values.push({value,label,identity});
  };
  if (selected) {
    const identity = selectedNormalized.type === "user" ? `user:${selectedNormalized.id}` : selectedNormalized.type;
    push(selected, ownerLabel(panel,selected), identity);
  }
  push(UNRETAINED_SCOPE, "No retained personal data", "unretained");
  push(SHARED_SCOPE, "Shared household", "shared");
  for (const user of voiceUsers(panel)) push(user.id, user.name, `user:${user.id}`);
  return values.map((item) => `<option value="${escapeValue(panel,item.value)}" ${item.value === selected ? "selected" : ""}>${escapeValue(panel,item.label)}</option>`).join("");
}

function mappingEntries(config) {
  const mappings = config?.voice_device_mappings;
  return mappings && typeof mappings === "object" && !Array.isArray(mappings) ? Object.entries(mappings) : [];
}

function mappingRow(panel, deviceId = "", owner = UNRETAINED_SCOPE) {
  return `<article class="voice-mapping-row" data-voice-mapping-row><label>Device ID<input class="voice-device-id" type="text" value="${escapeValue(panel,deviceId)}" placeholder="Home Assistant voice device ID" autocomplete="off"></label><label>Use retained data for<select class="voice-mapping-owner">${ownerOptions(panel,owner)}</select></label><button type="button" class="danger remove-voice-mapping">Remove</button></article>`;
}

export function voiceIdentitySummary(config = {}, users = []) {
  const userName = (value) => {
    const id = rawUserId(value);
    return users.find((user) => user.id === id)?.name || (id ? `unavailable user ${id}` : "no user selected");
  };
  const target = (policy) => {
    if (policy === "shared") return "shared household data";
    if (policy === "default_user") return config.voice_default_user_id ? `the default user (${userName(config.voice_default_user_id)})` : "the default user, but none is selected — so no personal data is retained";
    if (policy === "device_mapping") return "a device assignment";
    return "no retained personal data";
  };
  const main = String(config.voice_scope_policy || "unretained");
  if (main !== "device_mapping") return `Unidentified voice requests use ${target(main)}.`;
  const count = mappingEntries(config).length;
  const fallback = String(config.voice_unmapped_policy || "unretained");
  return `Unidentified voice requests use ${count} saved device assignment${count === 1 ? "" : "s"}. Devices without an assignment use ${target(fallback === "device_mapping" ? "unretained" : fallback)}.`;
}

function defaultUserMarkup(panel, config) {
  const selected = String(config.voice_default_user_id || "");
  return `<section class="voice-default-card" data-voice-default-card><h3>Default voice user</h3><p>Used only when an unidentified-voice policy explicitly chooses the default user.</p><div class="setting" data-field="voice_default_user_id" data-setting><label for="config-voice_default_user_id">Home Assistant user</label><select id="config-voice_default_user_id" data-config="voice_default_user_id">${defaultUserOptions(panel,selected)}</select><small>Choose by name; the existing Home Assistant user ID remains the stored value.</small><span class="field-error" data-error="voice_default_user_id"></span></div></section>`;
}

function mappingsMarkup(panel, config) {
  const entries = mappingEntries(config);
  return `<section id="voice-device-assignments" class="voice-mappings-card" data-voice-mappings-card data-setting><div class="section-heading"><div><h3>Voice device assignments</h3><p>Assign a source voice device to one Home Assistant user, the shared household, or no retained personal data.</p></div><button type="button" class="secondary" id="add-voice-mapping">+ Add assignment</button></div><div id="voice-mapping-list" class="voice-mapping-list">${entries.length ? entries.map(([deviceId,owner]) => mappingRow(panel,deviceId,owner)).join("") : '<div class="voice-mapping-empty">No device assignments saved. If device mapping is selected above, the unmapped-device fallback will be used.</div>'}</div><textarea id="voice-mappings" hidden aria-hidden="true">${escapeValue(panel,JSON.stringify(config.voice_device_mappings || {},null,2))}</textarea><span class="field-error" data-error="voice_device_mappings"></span><small>Assignments use only the source device ID supplied by Home Assistant. They do not infer who is speaking from presence, room, Bluetooth, cameras, or other signals.</small></section>`;
}

export function renderVoiceIdentity(panel) {
  const config = panel?._draft || panel?._result?.config || {};
  const users = voiceUsers(panel);
  return `${VOICE_STYLE}<div class="config-section-heading"><p class="eyebrow">Voice & identity</p><p>Choose whose memories and conversation history may be used when Home Assistant does not identify the speaker.</p></div><div class="voice-identity-intro"><div class="voice-identity-flow"><div class="voice-flow-step"><strong>1 · Signed-in identity wins</strong><small>If Home Assistant supplies an authenticated user, that user's personal scope is used regardless of the settings below.</small></div><div class="voice-flow-step"><strong>2 · Otherwise use the voice policy</strong><small>Unidentified requests can use no retained personal data, shared household data, a default user, or a device assignment.</small></div><div class="voice-flow-step"><strong>3 · No identity guessing</strong><small>Device assignments use Home Assistant's source device ID only. The integration does not guess a person from room or presence data.</small></div></div><div class="notice on voice-current-summary"><strong>Current unidentified voice behavior</strong><p id="voice-current-summary">${escapeValue(panel,voiceIdentitySummary(config,users))}</p></div></div><div class="voice-policy-grid"><section class="voice-policy-card"><h3>Unidentified voice requests</h3><p>This applies only when Home Assistant has not already attached a user to the request.</p>${policySelect(panel,"voice_scope_policy","Use retained data from",String(config.voice_scope_policy || "unretained"),"Choose the data owner for unidentified voice requests.")}</section><section class="voice-policy-card" data-voice-fallback-card><h3>Unmapped-device fallback</h3><p>Used only when device assignment is selected and the source device has no saved assignment.</p>${policySelect(panel,"voice_unmapped_policy","If the device is not assigned",String(config.voice_unmapped_policy || "unretained"),"Choose the safe fallback for an unidentified device.",true)}</section></div>${defaultUserMarkup(panel,config)}${mappingsMarkup(panel,config)}`;
}

export function transformVoiceIdentity(panel, html, documentRef = globalThis.document) {
  if (!documentRef?.createElement) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  const section = template.content.querySelector("#config-voice");
  if (!section) return html;
  section.innerHTML = renderVoiceIdentity(panel);
  return template.innerHTML;
}

function mappingRowsToObject(root) {
  const mapping = {};
  let duplicate = null;
  for (const row of root.querySelectorAll("[data-voice-mapping-row]")) {
    const deviceId = row.querySelector(".voice-device-id")?.value.trim() || "";
    if (!deviceId) continue;
    if (Object.prototype.hasOwnProperty.call(mapping,deviceId)) duplicate ||= deviceId;
    mapping[deviceId] = row.querySelector(".voice-mapping-owner")?.value || UNRETAINED_SCOPE;
  }
  return {mapping,duplicate};
}

function updateVoiceDependencies(panel) {
  const root = panel.shadowRoot;
  const main = root.querySelector('[data-config="voice_scope_policy"]')?.value || "unretained";
  const fallback = root.querySelector('[data-config="voice_unmapped_policy"]')?.value || "unretained";
  const mappingActive = main === "device_mapping";
  const defaultActive = main === "default_user" || (mappingActive && fallback === "default_user");
  const fallbackCard = root.querySelector("[data-voice-fallback-card]");
  const defaultCard = root.querySelector("[data-voice-default-card]");
  const mappingsCard = root.querySelector("[data-voice-mappings-card]");
  fallbackCard?.classList.toggle("is-disabled",!mappingActive);
  defaultCard?.classList.toggle("is-disabled",!defaultActive);
  mappingsCard?.classList.toggle("is-disabled",!mappingActive);
  fallbackCard?.querySelectorAll("select,input,button").forEach((control) => { control.disabled = !mappingActive; });
  defaultCard?.querySelectorAll("select,input,button").forEach((control) => { control.disabled = !defaultActive; });
  mappingsCard?.querySelectorAll("select,input,button").forEach((control) => { control.disabled = !mappingActive; });

  const config = {...(panel._draft || panel._result?.config || {})};
  config.voice_scope_policy = main;
  config.voice_unmapped_policy = fallback;
  config.voice_default_user_id = root.querySelector('[data-config="voice_default_user_id"]')?.value || config.voice_default_user_id || "";
  const currentMappings = mappingRowsToObject(root).mapping;
  config.voice_device_mappings = currentMappings;
  const summary = root.querySelector("#voice-current-summary");
  if (summary) summary.textContent = voiceIdentitySummary(config,voiceUsers(panel));
}

function syncVoiceMappings(panel) {
  const root = panel.shadowRoot;
  const hidden = root.querySelector("#voice-mappings");
  if (!hidden) return;
  const {mapping,duplicate} = mappingRowsToObject(root);
  const error = root.querySelector('[data-error="voice_device_mappings"]');
  if (error) error.textContent = duplicate ? `Device ID “${duplicate}” appears more than once.` : "";
  hidden.value = duplicate ? JSON.stringify(`duplicate device id: ${duplicate}`) : JSON.stringify(mapping,null,2);
  hidden.dispatchEvent(new Event("input",{bubbles:true}));
  updateVoiceDependencies(panel);
}

function bindMappingRows(panel) {
  const root = panel.shadowRoot;
  root.querySelectorAll("[data-voice-mapping-row]").forEach((row) => {
    if (row.dataset.voiceBound !== undefined) return;
    row.dataset.voiceBound = "";
    row.querySelector(".voice-device-id")?.addEventListener("input",() => syncVoiceMappings(panel));
    row.querySelector(".voice-mapping-owner")?.addEventListener("change",() => syncVoiceMappings(panel));
    row.querySelector(".remove-voice-mapping")?.addEventListener("click",() => {
      row.remove();
      if (!root.querySelector("[data-voice-mapping-row]")) root.querySelector("#voice-mapping-list").innerHTML = '<div class="voice-mapping-empty">No device assignments saved. If device mapping is selected above, the unmapped-device fallback will be used.</div>';
      syncVoiceMappings(panel);
    });
  });
}

export function bindVoiceIdentity(panel) {
  const root = panel.shadowRoot;
  root.querySelector('[data-config="voice_scope_policy"]')?.addEventListener("change",() => updateVoiceDependencies(panel));
  root.querySelector('[data-config="voice_unmapped_policy"]')?.addEventListener("change",() => updateVoiceDependencies(panel));
  root.querySelector('[data-config="voice_default_user_id"]')?.addEventListener("change",() => updateVoiceDependencies(panel));
  root.querySelector("#add-voice-mapping")?.addEventListener("click",() => {
    const list = root.querySelector("#voice-mapping-list");
    list.querySelector(".voice-mapping-empty")?.remove();
    list.insertAdjacentHTML("beforeend",mappingRow(panel));
    bindMappingRows(panel);
    updateVoiceDependencies(panel);
    list.querySelector("[data-voice-mapping-row]:last-child .voice-device-id")?.focus();
  });
  bindMappingRows(panel);
  updateVoiceDependencies(panel);
}

export {SHARED_SCOPE, UNRETAINED_SCOPE, VOICE_POLICY_LABELS};
