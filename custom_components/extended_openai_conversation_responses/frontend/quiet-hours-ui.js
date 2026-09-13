import {NAVIGATION, SETTINGS_INDEX} from "./frontend-navigation.js";

const PATCHED = Symbol.for("extended-openai.quiet-hours-ui");
const VIEW = "capabilities/quiet-hours";

function installNavigation() {
  const capabilities = NAVIGATION.find((item) => item.id === "capabilities");
  if (capabilities && !capabilities.sections.some((item) => item.id === "quiet-hours")) {
    const guestIndex = capabilities.sections.findIndex((item) => item.id === "guest-mode");
    const item = {
      id: "quiet-hours",
      label: "Quiet Hours",
      description: "Keep Assist satellites quieter on a schedule and expose the active period to Home Assistant automations.",
    };
    if (guestIndex >= 0) capabilities.sections.splice(guestIndex, 0, item);
    else capabilities.sections.push(item);
  }
  if (!SETTINGS_INDEX.some((item) => item.section === "quiet-hours")) {
    SETTINGS_INDEX.push({
      label: "Quiet Hours",
      description: "Schedule a maximum Assist satellite volume and optional wake-sound policy.",
      terms: "quiet hours night mode satellite volume wake sound",
      page: "capabilities",
      section: "quiet-hours",
      configKey: null,
      target: null,
      source: "quiet-hours",
    });
  }
}

function entityLabel(hass, entityId) {
  const state = hass?.states?.[entityId];
  return state?.attributes?.friendly_name || entityId;
}

function entityOptions(panel, domain, selected = "") {
  const entries = Object.keys(panel._hass?.states || {})
    .filter((entityId) => entityId.startsWith(`${domain}.`))
    .sort((a, b) => entityLabel(panel._hass, a).localeCompare(entityLabel(panel._hass, b)));
  return entries.map((entityId) => `<option value="${panel._e(entityId)}" ${entityId === selected ? "selected" : ""}>${panel._e(entityLabel(panel._hass, entityId))} · ${panel._e(entityId)}</option>`).join("");
}

function manualOverride(config, satelliteId) {
  return config?.overrides?.[satelliteId] || {};
}

function satelliteCard(panel, satellite, config) {
  const override = manualOverride(config, satellite.satellite_entity_id);
  const media = override.media_player_entity_id || "";
  const wake = override.wake_sound_entity_id || "";
  const autoMedia = satellite.media_player_source === "auto" ? satellite.media_player_entity_id : null;
  const autoWake = satellite.wake_sound_source === "auto" ? satellite.wake_sound_entity_id : null;
  return `<article class="qh-satellite">
    <div class="qh-satellite-heading">
      <div><strong>${panel._e(satellite.name)}</strong><small>${panel._e(satellite.satellite_entity_id)}</small></div>
      <span class="${satellite.media_player_entity_id ? "availability-badge" : "disabled-badge"}">${satellite.media_player_entity_id ? "Volume ready" : "No volume found"}</span>
    </div>
    <div class="qh-grid">
      <label><span>Volume entity</span><select class="qh-override" data-satellite="${panel._e(satellite.satellite_entity_id)}" data-kind="media_player_entity_id"><option value="">Auto${autoMedia ? ` · ${panel._e(autoMedia)}` : ""}</option>${entityOptions(panel, "media_player", media)}</select><small>${satellite.media_player_source === "manual" ? "Manual override" : autoMedia ? `Auto-discovered on the same device: ${panel._e(autoMedia)}` : "No same-device volume control was discovered. Choose one manually if this satellite has an output entity elsewhere."}</small></label>
      <label><span>Wake sound switch</span><select class="qh-override" data-satellite="${panel._e(satellite.satellite_entity_id)}" data-kind="wake_sound_entity_id"><option value="">Auto${autoWake ? ` · ${panel._e(autoWake)}` : ""}</option>${entityOptions(panel, "switch", wake)}</select><small>${satellite.wake_sound_source === "manual" ? "Manual override" : autoWake ? `Auto-discovered on the same device: ${panel._e(autoWake)}` : "No Wake sound switch was discovered. This is expected on many non-Voice-PE satellites."}</small></label>
    </div>
  </article>`;
}

export function renderQuietHours(panel) {
  const result = panel._result || {};
  const config = panel._quietHoursDraft || result.config || {};
  const satellites = result.satellites || [];
  const maxPercent = Math.round(Number(config.max_volume ?? 0.2) * 100);
  return `<section class="page-intro"><h1>Quiet Hours</h1><p>Apply a simple night-time policy to Assist satellites. Extended OpenAI handles only the common volume and wake-sound controls; use the Quiet Hours entity in normal Home Assistant automations for LEDs or other device-specific behaviour.</p></section>
    <section class="content-card">
      <div class="qh-status"><div><strong>${result.active ? "Quiet Hours active" : "Quiet Hours inactive"}</strong><small>${config.enabled ? `${panel._e(config.start || "22:00")}–${panel._e(config.end || "07:00")}` : "Schedule disabled"}</small></div><span class="${result.active ? "availability-badge" : "disabled-badge"}">${result.active ? "On" : "Off"}</span></div>
      <div class="config-toggle setting"><span class="setting-copy"><span class="setting-label-row"><label for="qh-enabled"><strong>Enable Quiet Hours</strong></label></span><small>Apply the policy every day between the configured start and end times.</small></span><label class="switch-control" for="qh-enabled"><input id="qh-enabled" type="checkbox" role="switch" ${config.enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label></div>
      <div class="qh-grid qh-policy">
        <label><span>Start</span><input id="qh-start" type="time" value="${panel._e(config.start || "22:00")}"></label>
        <label><span>End</span><input id="qh-end" type="time" value="${panel._e(config.end || "07:00")}"></label>
        <label><span>Maximum satellite volume</span><div class="qh-volume"><input id="qh-volume" type="range" min="0" max="100" step="1" value="${maxPercent}"><output id="qh-volume-value">${maxPercent}%</output></div><small>Acts as a ceiling: a satellite already quieter than this is never made louder.</small></label>
        <label><span>Wake sound during Quiet Hours</span><select id="qh-wake"><option value="off" ${config.wake_sound === "off" ? "selected" : ""}>Off</option><option value="on" ${config.wake_sound === "on" ? "selected" : ""}>On</option><option value="unchanged" ${config.wake_sound === "unchanged" ? "selected" : ""}>Don't change</option></select><small>Applied only where a compatible Wake sound switch is discovered or manually selected.</small></label>
      </div>
      <div class="qh-entity-note"><strong>Automation state</strong><code>${panel._e(result.state_entity_id || "binary_sensor.extended_openai_quiet_hours")}</code><small>This read-only entity is on for the scheduled period, even when every satellite is already below the volume ceiling. Use it to trigger your own LED or other satellite-specific automations.</small></div>
    </section>
    <section class="content-card"><div class="section-heading"><div><h2>Assist satellites</h2><p>Controls on the same Home Assistant device are discovered automatically. Manual choices below override discovery for that satellite only.</p></div></div>${satellites.length ? `<div class="qh-satellites">${satellites.map((satellite) => satelliteCard(panel, satellite, config)).join("")}</div>` : `<p class="empty">No Assist satellite entities were found.</p>`}</section>
    <div class="config-actions"><button id="qh-save" type="button" class="primary">Save Quiet Hours</button><button id="qh-reset" type="button">Reset unsaved changes</button></div>`;
}

function setOverride(panel, satelliteId, kind, value) {
  const draft = panel._quietHoursDraft;
  if (!draft) return;
  draft.overrides ||= {};
  const current = {...(draft.overrides[satelliteId] || {})};
  if (value) current[kind] = value;
  else delete current[kind];
  if (current.media_player_entity_id || current.wake_sound_entity_id) draft.overrides[satelliteId] = current;
  else delete draft.overrides[satelliteId];
}

export function bindQuietHours(panel) {
  const root = panel.shadowRoot;
  root.querySelector("#qh-enabled")?.addEventListener("change", (event) => { panel._quietHoursDraft.enabled = event.target.checked; });
  root.querySelector("#qh-start")?.addEventListener("change", (event) => { panel._quietHoursDraft.start = event.target.value; });
  root.querySelector("#qh-end")?.addEventListener("change", (event) => { panel._quietHoursDraft.end = event.target.value; });
  root.querySelector("#qh-wake")?.addEventListener("change", (event) => { panel._quietHoursDraft.wake_sound = event.target.value; });
  const volume = root.querySelector("#qh-volume");
  volume?.addEventListener("input", (event) => {
    const percent = Number(event.target.value);
    panel._quietHoursDraft.max_volume = percent / 100;
    const output = root.querySelector("#qh-volume-value");
    if (output) output.textContent = `${percent}%`;
  });
  root.querySelectorAll(".qh-override").forEach((select) => select.addEventListener("change", () => setOverride(panel, select.dataset.satellite, select.dataset.kind, select.value)));
  root.querySelector("#qh-reset")?.addEventListener("click", () => {
    panel._quietHoursDraft = JSON.parse(JSON.stringify(panel._result?.config || {}));
    panel._render();
  });
  root.querySelector("#qh-save")?.addEventListener("click", async () => {
    const save = root.querySelector("#qh-save");
    if (save) save.disabled = true;
    try {
      const result = await panel._call("quiet_hours", "update", {config: panel._quietHoursDraft});
      panel._result = result;
      panel._quietHoursDraft = JSON.parse(JSON.stringify(result.config || {}));
      panel._toast("Quiet Hours saved");
      panel._render();
    } catch (err) {
      if (save) save.disabled = false;
      panel._toast(`Unable to save Quiet Hours: ${err.message || String(err)}`, true);
    }
  });
}

export function installQuietHoursUI(registry = globalThis.customElements) {
  installNavigation();
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalLoadSection = prototype._loadSection;
    prototype._loadSection = async function(silent = false) {
      if (this._viewKey() !== VIEW) return originalLoadSection.call(this, silent);
      const token = ++this._loadToken;
      if (!silent) { this._busy = true; this._render(); }
      try {
        const result = await this._call("quiet_hours", "get");
        if (token !== this._loadToken) return;
        this._result = result;
        this._quietHoursDraft = JSON.parse(JSON.stringify(result.config || {}));
        this._error = null;
      } catch (err) {
        if (token === this._loadToken) this._error = err.message || String(err);
      } finally {
        if (token === this._loadToken) { this._busy = false; this._render(); }
      }
    };

    const originalContent = prototype._content;
    prototype._content = function(agent) {
      if (this._viewKey() === VIEW) return renderQuietHours(this);
      return originalContent.call(this, agent);
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this, args);
      if (this._viewKey() === VIEW) bindQuietHours(this);
      return result;
    };

    const originalStyles = prototype._styles;
    prototype._styles = function(...args) {
      return `${originalStyles.apply(this, args)}
        .qh-status,.qh-satellite-heading{display:flex;justify-content:space-between;gap:16px;align-items:center}.qh-status{padding-bottom:18px;border-bottom:1px solid var(--divider-color);margin-bottom:8px}.qh-status div,.qh-satellite-heading div{display:flex;flex-direction:column;gap:3px}.qh-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.qh-grid label{display:flex;flex-direction:column;gap:7px}.qh-grid label>span{font-weight:600}.qh-grid small,.qh-entity-note small,.qh-satellite small{color:var(--secondary-text-color);line-height:1.45}.qh-policy{margin-top:20px}.qh-volume{display:flex;align-items:center;gap:12px}.qh-volume input{flex:1}.qh-volume output{min-width:44px;text-align:right;font-variant-numeric:tabular-nums}.qh-entity-note{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:20px;padding:14px;border-radius:10px;background:var(--secondary-background-color)}.qh-entity-note small{flex-basis:100%}.qh-satellites{display:grid;gap:14px}.qh-satellite{border:1px solid var(--divider-color);border-radius:12px;padding:16px}.qh-satellite-heading{margin-bottom:14px}.config-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:16px}@media(max-width:760px){.qh-grid{grid-template-columns:1fr}.qh-status,.qh-satellite-heading{align-items:flex-start}}`;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") installQuietHoursUI();
