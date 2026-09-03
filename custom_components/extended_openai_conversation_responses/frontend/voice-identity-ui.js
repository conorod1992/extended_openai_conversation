const SHARED_SCOPE = "shared:household";
const UNRETAINED_SCOPE = "unretained";

const POLICY_LABELS = Object.freeze({
  unretained: "Do not retain personal data",
  shared: "Use shared household data",
  default_user: "Use the default user",
  device_mapping: "Use a device assignment",
});

const VOICE_STYLE = `<style data-eoc-voice-identity>
  .voice-identity-flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:12px}
  .voice-flow-step,.voice-policy-card,.voice-default-card,.voice-mappings-card{padding:14px;border:1px solid var(--divider-color);border-radius:11px;background:var(--card-background-color)}
  .voice-flow-step{background:var(--secondary-background-color)}
  .voice-flow-step strong{display:block;margin-bottom:4px}.voice-flow-step small{display:block;color:var(--secondary-text-color);line-height:1.4}
  .voice-policy-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0}
  .voice-policy-card h3,.voice-default-card h3,.voice-mappings-card h3{margin:0 0 4px;font-size:15px}
  .voice-policy-card>p,.voice-default-card>p,.voice-mappings-card .section-heading p{margin:0 0 11px;color:var(--secondary-text-color)}
  .voice-policy-card .setting,.voice-default-card .setting{margin:0}.voice-default-card{margin-bottom:14px}
  .voice-policy-card.is-disabled,.voice-default-card.is-disabled,.voice-mappings-card.is-disabled{opacity:.7}
  .voice-mapping-list{display:grid;gap:9px;margin-top:11px}.voice-mapping-empty{padding:13px;border:1px dashed var(--divider-color);border-radius:9px;color:var(--secondary-text-color)}
  .voice-mapping-row{display:grid;grid-template-columns:minmax(160px,1fr) minmax(220px,1.2fr) auto;gap:10px;align-items:end;padding:11px;border:1px solid var(--divider-color);border-radius:9px;background:var(--primary-background-color)}
  .voice-mapping-row label{display:grid;gap:5px;font-size:13px;color:var(--secondary-text-color)}.voice-mapping-row input,.voice-mapping-row select{width:100%;box-sizing:border-box}
  @media(max-width:800px){.voice-identity-flow,.voice-policy-grid,.voice-mapping-row{grid-template-columns:1fr}.remove-voice-mapping{justify-self:start}}
</style>`;

const e = (panel,value) => panel._e(String(value ?? ""));
const rawUserId = (value) => String(value || "").replace(/^user:/,"");

export function voiceUsers(panel) {
  const scopes = panel?._baseScopes?.length ? panel._baseScopes : panel?._data?.scopes || [];
  const seen = new Set();
  const result = [];
  for (const scope of scopes) {
    if (scope?.scope_type !== "user") continue;
    const id = rawUserId(scope.scope_id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    result.push({id,name:String(scope.display_name || id)});
  }
  return result;
}

export function voiceUserLabel(panel,userId) {
  const id = rawUserId(userId);
  return voiceUsers(panel).find((user) => user.id === id)?.name || null;
}

function policyLabel(value,fallback=false) {
  if (fallback && value === "device_mapping") return "Device mapping again (no retained data)";
  return POLICY_LABELS[value] || String(value || "").replaceAll("_"," ");
}

function policySelect(panel,key,label,value,help,fallback=false) {
  const options = panel?._result?.options?.[key] || [];
  const values = options.map((item) => typeof item === "string" ? item : item.value);
  if (value && !values.includes(value)) values.push(value);
  return `<div class="setting" data-field="${key}" data-setting><label for="config-${key}">${e(panel,label)}</label><select id="config-${key}" data-config="${key}">${values.map((item) => `<option value="${e(panel,item)}" ${item === value ? "selected" : ""}>${e(panel,policyLabel(item,fallback))}</option>`).join("")}</select><small>${e(panel,help)}</small><span class="field-error" data-error="${key}"></span></div>`;
}

function defaultUserOptions(panel,selected) {
  const users = voiceUsers(panel);
  const unavailable = selected && !users.some((user) => user.id === selected)
    ? `<option value="${e(panel,selected)}" selected>Unavailable user · ${e(panel,selected)}</option>` : "";
  return `<option value="" ${selected ? "" : "selected"}>Not selected</option>${unavailable}${users.map((user) => `<option value="${e(panel,user.id)}" ${user.id === selected ? "selected" : ""}>${e(panel,user.name)}</option>`).join("")}`;
}

function normalizedOwner(owner) {
  const value = String(owner || "");
  if (value === "shared" || value === SHARED_SCOPE) return {type:"shared"};
  if (value === UNRETAINED_SCOPE) return {type:"unretained"};
  return {type:"user",id:rawUserId(value)};
}

function ownerLabel(panel,owner) {
  const normalized = normalizedOwner(owner);
  if (normalized.type === "shared") return "Shared household";
  if (normalized.type === "unretained") return "No retained personal data";
  return voiceUserLabel(panel,normalized.id) || `Unavailable user · ${owner}`;
}

function ownerOptions(panel,selected) {
  const options = [];
  const identities = new Set();
  const add = (value,label,identity) => { if (!identities.has(identity)) { identities.add(identity); options.push({value,label}); } };
  if (selected) {
    const normalized = normalizedOwner(selected);
    add(selected,ownerLabel(panel,selected),normalized.type === "user" ? `user:${normalized.id}` : normalized.type);
  }
  add(UNRETAINED_SCOPE,"No retained personal data","unretained");
  add(SHARED_SCOPE,"Shared household","shared");
  voiceUsers(panel).forEach((user) => add(user.id,user.name,`user:${user.id}`));
  return options.map((item) => `<option value="${e(panel,item.value)}" ${item.value === selected ? "selected" : ""}>${e(panel,item.label)}</option>`).join("");
}

function mappingEntries(config) {
  const value = config?.voice_device_mappings;
  return value && typeof value === "object" && !Array.isArray(value) ? Object.entries(value) : [];
}

function mappingRow(panel,deviceId="",owner=UNRETAINED_SCOPE) {
  return `<article class="voice-mapping-row" data-voice-mapping-row><label>Device ID<input class="voice-device-id" value="${e(panel,deviceId)}" placeholder="Home Assistant voice device ID" autocomplete="off"></label><label>Use retained data for<select class="voice-mapping-owner">${ownerOptions(panel,owner)}</select></label><button type="button" class="danger remove-voice-mapping">Remove</button></article>`;
}

export function voiceIdentitySummary(config={},users=[]) {
  const userName = (value) => users.find((user) => user.id === rawUserId(value))?.name || `unavailable user ${rawUserId(value)}`;
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

export function renderVoiceIdentity(panel) {
  const config = panel?._draft || panel?._result?.config || {};
  const entries = mappingEntries(config);
  const selectedUser = String(config.voice_default_user_id || "");
  return `${VOICE_STYLE}<div class="config-section-heading"><p class="eyebrow">Voice & identity</p><p>Choose whose memories and conversation history may be used when Home Assistant does not identify the speaker.</p></div>
    <div class="voice-identity-flow"><div class="voice-flow-step"><strong>1 · Signed-in identity wins</strong><small>If Home Assistant supplies an authenticated user, that user's personal scope is used regardless of the settings below.</small></div><div class="voice-flow-step"><strong>2 · Otherwise use the voice policy</strong><small>Unidentified requests can use no retained personal data, shared household data, a default user, or a device assignment.</small></div><div class="voice-flow-step"><strong>3 · No identity guessing</strong><small>Device assignments use Home Assistant's source device ID only; room, presence, Bluetooth, and camera data are not used to guess a speaker.</small></div></div>
    <div class="notice on"><strong>Current unidentified voice behavior</strong><p id="voice-current-summary">${e(panel,voiceIdentitySummary(config,voiceUsers(panel)))}</p></div>
    <div class="voice-policy-grid"><section class="voice-policy-card"><h3>Unidentified voice requests</h3><p>Applies only when Home Assistant has not already attached a user to the request.</p>${policySelect(panel,"voice_scope_policy","Use retained data from",String(config.voice_scope_policy || "unretained"),"Choose the data owner for unidentified voice requests.")}</section><section class="voice-policy-card" data-voice-fallback-card><h3>Unmapped-device fallback</h3><p>Used only when device assignment is selected and the source device has no saved assignment.</p>${policySelect(panel,"voice_unmapped_policy","If the device is not assigned",String(config.voice_unmapped_policy || "unretained"),"Choose the safe fallback for an unidentified device.",true)}</section></div>
    <section class="voice-default-card" data-voice-default-card><h3>Default voice user</h3><p>Used only when an unidentified-voice policy explicitly chooses the default user.</p><div class="setting" data-field="voice_default_user_id" data-setting><label for="config-voice_default_user_id">Home Assistant user</label><select id="config-voice_default_user_id" data-config="voice_default_user_id">${defaultUserOptions(panel,selectedUser)}</select><small>Choose by name; the existing Home Assistant user ID remains the stored value.</small><span class="field-error" data-error="voice_default_user_id"></span></div></section>
    <section id="voice-mappings" class="voice-mappings-card" data-voice-mappings-card data-setting><div class="section-heading"><div><h3>Voice device assignments</h3><p>Assign a source voice device to one Home Assistant user, the shared household, or no retained personal data.</p></div><button type="button" class="secondary" id="add-voice-mapping">+ Add assignment</button></div><div id="voice-mapping-list" class="voice-mapping-list">${entries.length ? entries.map(([deviceId,owner]) => mappingRow(panel,deviceId,owner)).join("") : '<div class="voice-mapping-empty">No device assignments saved. If device mapping is selected above, the unmapped-device fallback will be used.</div>'}</div><span class="field-error" data-error="voice_device_mappings"></span><small>Assignments use only the source device ID supplied by Home Assistant and never infer who is speaking.</small></section>`;
}

export function transformVoiceIdentity(panel,html,documentRef=globalThis.document) {
  if (!documentRef?.createElement) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  const section = template.content.querySelector("#config-voice");
  if (section) section.innerHTML = renderVoiceIdentity(panel);
  return template.innerHTML;
}

function rowsToMapping(root) {
  const mapping = {};
  let duplicate = null;
  root.querySelectorAll("[data-voice-mapping-row]").forEach((row) => {
    const deviceId = row.querySelector(".voice-device-id")?.value.trim() || "";
    if (!deviceId) return;
    if (Object.prototype.hasOwnProperty.call(mapping,deviceId)) duplicate ||= deviceId;
    mapping[deviceId] = row.querySelector(".voice-mapping-owner")?.value || UNRETAINED_SCOPE;
  });
  return {mapping,duplicate};
}

function currentConfig(panel) {
  const root = panel.shadowRoot;
  const config = {...(panel._draft || panel._result?.config || {})};
  config.voice_scope_policy = root.querySelector('[data-config="voice_scope_policy"]')?.value || "unretained";
  config.voice_unmapped_policy = root.querySelector('[data-config="voice_unmapped_policy"]')?.value || "unretained";
  config.voice_default_user_id = root.querySelector('[data-config="voice_default_user_id"]')?.value || "";
  config.voice_device_mappings = rowsToMapping(root).mapping;
  return config;
}

function updateDependencies(panel) {
  const root = panel.shadowRoot;
  const config = currentConfig(panel);
  const mappingActive = config.voice_scope_policy === "device_mapping";
  const defaultActive = config.voice_scope_policy === "default_user" || (mappingActive && config.voice_unmapped_policy === "default_user");
  for (const [selector,active] of [["[data-voice-fallback-card]",mappingActive],["[data-voice-default-card]",defaultActive],["[data-voice-mappings-card]",mappingActive]]) {
    const card = root.querySelector(selector);
    card?.classList.toggle("is-disabled",!active);
    card?.querySelectorAll("input,select,button").forEach((control) => { control.disabled = !active; });
  }
  const summary = root.querySelector("#voice-current-summary");
  if (summary) summary.textContent = voiceIdentitySummary(config,voiceUsers(panel));
}

function syncMappings(panel) {
  const root = panel.shadowRoot;
  const card = root.querySelector("#voice-mappings");
  if (!card) return;
  const {mapping,duplicate} = rowsToMapping(root);
  const error = root.querySelector('[data-error="voice_device_mappings"]');
  if (error) error.textContent = duplicate ? `Device ID “${duplicate}” appears more than once.` : "";
  card.value = duplicate ? JSON.stringify(`duplicate device id: ${duplicate}`) : JSON.stringify(mapping,null,2);
  card.dispatchEvent(new Event("input",{bubbles:true}));
  updateDependencies(panel);
}

function bindRows(panel) {
  const root = panel.shadowRoot;
  root.querySelectorAll("[data-voice-mapping-row]").forEach((row) => {
    if (row.dataset.voiceBound !== undefined) return;
    row.dataset.voiceBound = "";
    row.querySelector(".voice-device-id")?.addEventListener("input",() => syncMappings(panel));
    row.querySelector(".voice-mapping-owner")?.addEventListener("change",() => syncMappings(panel));
    row.querySelector(".remove-voice-mapping")?.addEventListener("click",() => {
      row.remove();
      if (!root.querySelector("[data-voice-mapping-row]")) root.querySelector("#voice-mapping-list").innerHTML = '<div class="voice-mapping-empty">No device assignments saved. If device mapping is selected above, the unmapped-device fallback will be used.</div>';
      syncMappings(panel);
    });
  });
}

export function bindVoiceIdentity(panel) {
  const root = panel.shadowRoot;
  const mappings = root.querySelector("#voice-mappings");
  if (mappings) mappings.value = JSON.stringify(rowsToMapping(root).mapping,null,2);
  root.querySelector('[data-config="voice_scope_policy"]')?.addEventListener("change",() => updateDependencies(panel));
  root.querySelector('[data-config="voice_unmapped_policy"]')?.addEventListener("change",() => updateDependencies(panel));
  root.querySelector('[data-config="voice_default_user_id"]')?.addEventListener("change",() => updateDependencies(panel));
  root.querySelector("#add-voice-mapping")?.addEventListener("click",() => {
    const list = root.querySelector("#voice-mapping-list");
    list.querySelector(".voice-mapping-empty")?.remove();
    list.insertAdjacentHTML("beforeend",mappingRow(panel));
    bindRows(panel); updateDependencies(panel);
    list.querySelector("[data-voice-mapping-row]:last-child .voice-device-id")?.focus();
  });
  bindRows(panel); updateDependencies(panel);
}

export {SHARED_SCOPE,UNRETAINED_SCOPE,POLICY_LABELS};
