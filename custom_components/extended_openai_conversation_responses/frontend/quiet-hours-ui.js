import {ensureNativePicker} from "./native-picker-loader.js";
import {captureConfigurationOwner, configurationSubmissionIsCurrent} from "./configuration-acknowledgement.js";
import {capturePageRead, acceptPageRead} from "./page-save-acknowledgement.js";

export async function loadQuietHours(panel, silent = false) {
  const token = ++panel._loadToken;
  const owner = captureConfigurationOwner(panel), generation = panel._cacheGeneration;
  const read = capturePageRead(panel, VIEW);
  const current = () => token === panel._loadToken && panel._viewKey?.() === VIEW
    && panel.isConnected !== false && configurationSubmissionIsCurrent(panel, owner);
  if (!silent) { panel._busy = true; panel._render(); }
  try {
    const result = await panel._call("quiet_hours", "get");
    if (!current() || generation !== panel._cacheGeneration) return;
    panel._result = result;
    if (!panel._unsavedState?.scopes.get(VIEW)?.isCurrent?.()) panel._quietHoursDraft = JSON.parse(JSON.stringify(result.config || {}));
    panel._error = null;
    acceptPageRead(panel, result, read);
  } catch (err) {
    if (current() && generation === panel._cacheGeneration) panel._error = err.message || String(err);
  } finally {
    if (current()) {
      if (generation !== panel._cacheGeneration) queueMicrotask(() => { if (current()) void loadQuietHours(panel, silent); });
      else { panel._busy = false; panel._render(); }
    }
  }
}

const VIEW = "capabilities/quiet-hours";
const QUIET_HOURS_STYLES = `
      .qh-override[hidden]{display:none}
      .qh-satellite-summary{display:grid;gap:5px;overflow-wrap:anywhere;color:var(--secondary-text-color)}.qh-satellite-config{margin-top:12px;padding-top:6px}.qh-satellite-config>summary{min-height:42px}

      .qh-status,.qh-satellite-heading{display:flex;justify-content:space-between;gap:16px;align-items:center}.qh-status{padding-bottom:18px;border-bottom:1px solid var(--divider-color);margin-bottom:8px}.qh-status div,.qh-satellite-heading div{display:flex;flex-direction:column;gap:3px}.qh-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.qh-grid label{display:flex;flex-direction:column;gap:7px}.qh-grid label>span{font-weight:600}.qh-override{display:block;width:100%;min-width:0;max-width:100%}.qh-grid>*{min-width:0}.qh-satellite,.qh-satellite-config,.qh-satellite-heading>div,.qh-status>div{min-width:0;max-width:100%}.qh-volume input{min-width:0}.qh-satellite-heading,.qh-status{flex-wrap:wrap}.qh-grid small,.qh-entity-note small,.qh-satellite small{color:var(--secondary-text-color);line-height:1.45}.qh-policy{margin-top:20px}.qh-volume{display:flex;align-items:center;gap:12px}.qh-volume input{flex:1}.qh-volume output{min-width:44px;text-align:right;font-variant-numeric:tabular-nums}.qh-entity-note{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:20px;padding:14px;border-radius:10px;background:var(--secondary-background-color)}.qh-entity-note small{flex-basis:100%}.qh-entity-note code{min-width:0;max-width:100%;overflow-wrap:anywhere;white-space:normal}.qh-satellites{display:grid;gap:14px}.qh-satellite{border:1px solid var(--divider-color);border-radius:12px;padding:16px}.qh-satellite-heading{margin-bottom:14px}.config-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:16px}@media(max-width:760px){.qh-grid{grid-template-columns:1fr}.qh-status,.qh-satellite-heading{align-items:flex-start}}`;

function manualOverride(config, satelliteId) {
  return config?.overrides?.[satelliteId] || {};
}

function volumeStatus(panel, satellite) {
  const entityId = satellite.media_player_entity_id;
  if (!entityId) return {label: "No compatible speaker found", ready: false};
  const state = panel._hass?.states?.[entityId];
  const volume = state?.attributes?.volume_level;
  if (!state || state.state === "unavailable" || state.state === "unknown" || typeof volume !== "number" || !Number.isFinite(volume)) {
    return {label: "Speaker temporarily unavailable", ready: false};
  }
  return {label: "Speaker ready", ready: true};
}

// Summaries use the draft's selected entities. Clearing a saved manual override
// does not guess what the backend will detect automatically after Save.
export function quietHoursSatellitePresentation(panel, satellite, config) {
  const override = manualOverride(config, satellite.satellite_entity_id);
  const saved = manualOverride(panel._result?.config, satellite.satellite_entity_id);
  const selected = (key, source) => {
    if (override[key]) return {id: override[key], manual: true, pending: false};
    if (saved[key]) return {id: "", manual: false, pending: true};
    return {id: satellite[key] || "", manual: source === "manual", pending: false};
  };
  const media = selected("media_player_entity_id", satellite.media_player_source);
  const wake = selected("wake_sound_entity_id", satellite.wake_sound_source);
  const label = (choice) => {
    if (choice.pending) return "Automatic — resolved after saving";
    const friendly = panel._hass?.states?.[choice.id]?.attributes?.friendly_name;
    const entity = friendly ? `${friendly} (${choice.id})` : choice.id;
    return `${choice.manual ? "Manual" : "Automatic"} · ${entity}`;
  };
  const mediaReview = !media.id && !media.pending && (satellite.media_player_candidates || []).length > 1;
  const speaker = media.pending ? {label: "Speaker selection pending save", ready: false}
    : mediaReview ? {label: "Manual selection required", ready: false}
    : volumeStatus(panel, {media_player_entity_id: media.id});
  const wakeState = panel._hass?.states?.[wake.id]?.state;
  const wakeUnavailable = Boolean(wake.id) && (!wakeState || ["unknown", "unavailable"].includes(wakeState));
  const wakeReview = !wake.id && !wake.pending && satellite.wake_sound_ambiguous === true;
  const wakeUsed = config.wake_sound !== "unchanged";
  return {
    media, wake, speaker,
    mediaSummary: media.id ? `${label(media)}${speaker.ready ? "" : " — temporarily unavailable"}`
      : media.pending ? label(media)
      : mediaReview ? `Manual selection required · ${satellite.media_player_candidates.length} media players on this device`
      : "No compatible speaker found",
    wakeSummary: wake.id ? `${label(wake)}${wakeUnavailable ? " — temporarily unavailable" : ""}`
      : wake.pending ? label(wake)
      : wakeReview ? "Manual selection required · multiple recognised wake-word switches"
      : "No wake-word switch found (optional)",
    needsAttention: (!media.pending && !speaker.ready) || (wakeUsed && (wakeUnavailable || wakeReview)),
  };
}

function pickerHelp(satellite, media) {
  const {source, automatic, candidates} = pickerContext(satellite, media);
  if (source === "manual") return media ? "Manually selected for this satellite." : "Confirm this controls the wake-word chime.";
  if (automatic) return `Found automatically: ${automatic}`;
  if (media) return candidates.length ? "Choose a speaker on this device." : "This device has no media player entity.";
  return candidates.length ? "Choose the wake-word chime switch." : "This device has no switch for the optional chime.";
}

function satelliteCard(panel, satellite, config) {
  const presentation = quietHoursSatellitePresentation(panel, satellite, config);
  const speakerStatus = presentation.speaker;
  return `<article class="qh-satellite" data-qh-satellite="${panel._e(satellite.satellite_entity_id)}" ${presentation.needsAttention ? "data-qh-attention" : ""}>
    <div class="qh-satellite-heading">
      <div><strong>${panel._e(satellite.name)}</strong><small>${panel._e(satellite.satellite_entity_id)}</small></div>
      <span data-qh-speaker-status class="${speakerStatus.ready ? "availability-badge" : "disabled-badge"}">${speakerStatus.label}</span>
    </div>
    <div class="qh-satellite-summary"><span>Speaker: <span data-qh-media-summary>${panel._e(presentation.mediaSummary)}</span></span><span>Wake-word switch: <span data-qh-wake-summary>${panel._e(presentation.wakeSummary)}</span></span></div>
    <details class="qh-satellite-config eoc-details-base" ${presentation.needsAttention ? "open" : ""}><summary>Configure</summary><div class="qh-grid">
      <label><span>Speaker volume entity</span><ha-entity-picker class="qh-override" data-satellite="${panel._e(satellite.satellite_entity_id)}" data-kind="media_player_entity_id" data-domain="media_player"></ha-entity-picker><small>${panel._e(pickerHelp(satellite, true))}</small></label>
      <label><span>Wake-word sound switch</span><ha-entity-picker class="qh-override" data-satellite="${panel._e(satellite.satellite_entity_id)}" data-kind="wake_sound_entity_id" data-domain="switch"></ha-entity-picker><small>${panel._e(pickerHelp(satellite, false))}</small></label>
    </div></details>
  </article>`;
}

function quietHoursStatus(panel) {
  const {active, config = {}} = panel._result || {};
  const title = active ? "Quiet Hours active now" : config.enabled ? "Outside Quiet Hours" : "Quiet Hours schedule disabled";
  const detail = config.enabled ? `${panel._e(config.start || "22:00")}–${panel._e(config.end || "07:00")} every day` : "The saved schedule is currently turned off.";
  return `<div><strong data-qh-live-title>${title}</strong><small>${detail}</small></div><span data-qh-live-badge class="${active ? "availability-badge" : "neutral-badge"}">${active ? "Active" : "Inactive"}</span>`;
}

const refreshable = (root, control, blurred) => blurred ? control === blurred : control !== root.activeElement;

function refreshScheduleControls(panel, blurred) {
  const root = panel.shadowRoot, config = panel._quietHoursDraft;
  if (!config) return;
  const values = {
    enabled: Boolean(config.enabled), start: config.start ?? "22:00",
    end: config.end ?? "07:00", wake: config.wake_sound ?? "off",
    volume: Math.round(Number(config.max_volume ?? 0.2) * 100),
  };
  for (const [name, value] of Object.entries(values)) {
    const input = root.querySelector("#qh-" + name);
    if (input && refreshable(root, input, blurred)) {
      input[name === "enabled" ? "checked" : "value"] = value;
    }
  }
  const output = root.querySelector("#qh-volume-value");
  if (output) output.textContent = root.querySelector("#qh-volume").value + "%";
}

export function refreshTimedStatus(panel) {
  const node = panel.shadowRoot.querySelector(".qh-status");
  if (node) node.innerHTML = quietHoursStatus(panel);
  refreshScheduleControls(panel);
  refreshSatelliteControls(panel);
}

export function timedStructure(result) {
  return [result?.available, result?.state_entity_id, result?.satellites?.map(item => [item.satellite_entity_id, item.name])];
}

export function renderQuietHours(panel) {
  const result = panel._result || {};
  if (result.available === false) {
    return `<section class="page-intro"><h1>Quiet Hours</h1></section>
      <section class="content-card" role="alert"><h2>Quiet Hours unavailable</h2>
      <p>${panel._e(result.error || "Repair Quiet Hours storage and restart Home Assistant.")}</p>
      <p>Quiet Hours cannot change devices or save settings.</p></section>`;
  }
  const savedConfig = result.config || {};
  const config = panel._quietHoursDraft || savedConfig;
  const satellites = result.satellites || [];
  const maxPercent = Math.round(Number(config.max_volume ?? 0.2) * 100);
  return `<style>${QUIET_HOURS_STYLES}</style><section class="page-intro"><h1>Quiet Hours</h1><p>Schedule quieter Assist speakers and wake-word chimes.</p></section>
    <section class="content-card">
      <div class="qh-status">${quietHoursStatus(panel)}</div>
      <div class="config-toggle setting"><span class="setting-copy"><span class="setting-label-row"><label for="qh-enabled"><strong>Enable daily schedule</strong></label></span><small>Enabling within the schedule takes effect immediately.</small></span><label class="switch-control" for="qh-enabled"><input id="qh-enabled" type="checkbox" role="switch" ${config.enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label></div>
      <div class="qh-grid qh-policy">
        <label><span>Start</span><input id="qh-start" type="time" value="${panel._e(config.start || "22:00")}"></label>
        <label><span>End</span><input id="qh-end" type="time" value="${panel._e(config.end || "07:00")}"></label>
        <label><span>Starting volume limit</span><div class="qh-volume"><input id="qh-volume" type="range" min="0" max="100" step="1" value="${maxPercent}"><output id="qh-volume-value">${maxPercent}%</output></div><small>At startup, louder satellites are lowered to this level. The quieter ones stay unchanged. Satellites appearing later may also be adjusted. Later volume changes by you or automations are respected. Volume is restored only if it was not independently changed. Changing this limit mid-period may briefly restore previous volumes. Other audio played on the satellite is affected too.</small></label>
        <label><span>Wake-word sound</span><select id="qh-wake"><option value="off" ${config.wake_sound === "off" ? "selected" : ""}>Off during Quiet Hours</option><option value="on" ${config.wake_sound === "on" ? "selected" : ""}>On during Quiet Hours</option><option value="unchanged" ${config.wake_sound === "unchanged" ? "selected" : ""}>Don't change it</option></select><small>The chime played when a satellite hears its wake word. Requires a compatible switch.</small></label>
      </div>
      <div class="qh-entity-note"><strong>Use Quiet Hours in automations</strong><code>${panel._e(result.state_entity_id || "binary_sensor.extended_openai_quiet_hours")}</code><small>This entity stays on throughout Quiet Hours. Use these actions to change the schedule <code>enable_quiet_hours</code> and <code>disable_quiet_hours</code>.</small></div>
    </section>
    <section class="content-card"><div class="section-heading"><div><h2>Assist satellites</h2><p>Choose controls manually if discovery is ambiguous. Quiet Hours keeps control until the period ends and respects manual changes.</p></div></div>${satellites.length ? `<div class="qh-satellites">${satellites.map((satellite) => satelliteCard(panel, satellite, config)).join("")}</div>` : `<p class="empty">Quiet Hours will keep checking and will pick them up as they become available.</p>`}</section>
    `;
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

function refreshSatelliteSummary(panel, card, satellite) {
  if (!card || !satellite) return;
  const result = quietHoursSatellitePresentation(panel, satellite, panel._quietHoursDraft || {});
  card.querySelector("[data-qh-media-summary]").textContent = result.mediaSummary;
  card.querySelector("[data-qh-wake-summary]").textContent = result.wakeSummary;
  const status = card.querySelector("[data-qh-speaker-status]");
  status.textContent = result.speaker.label;
  status.className = result.speaker.ready ? "availability-badge" : "disabled-badge";
  if (result.needsAttention && !card.hasAttribute("data-qh-attention")) card.querySelector(".qh-satellite-config").open = true;
  card.toggleAttribute("data-qh-attention", result.needsAttention);
}

function satelliteFor(panel, id) {
  return panel._result?.satellites?.find(item => item.satellite_entity_id === id);
}

function pickerContext(satellite, media) {
  const source = media ? satellite?.media_player_source : satellite?.wake_sound_source;
  return {
    source,
    automatic: source === "auto" && (media ? satellite.media_player_entity_id : satellite.wake_sound_entity_id),
    candidates: (media ? satellite?.media_player_candidates : satellite?.wake_sound_candidates) || [],
  };
}

function projectPicker(panel, picker, satellite) {
  const {kind, satellite: id} = picker.dataset;
  const media = kind === "media_player_entity_id";
  const {candidates, automatic} = pickerContext(satellite, media);
  const selected = manualOverride(panel._quietHoursDraft, id)[kind] || "";
  picker.includeEntities = selected && !candidates.includes(selected) ? [...candidates, selected] : candidates;
  picker.placeholder = automatic ? `Automatic · ${automatic}` : "Automatic";
  if (picker.value !== selected) picker.value = selected;
  const help = picker.parentElement?.lastElementChild;
  if (help) help.textContent = pickerHelp(satellite, media);
}

function refreshSatelliteControls(panel, blurred) {
  const root = panel.shadowRoot;
  for (const card of root.querySelectorAll("[data-qh-satellite]")) {
    const satellite = satelliteFor(panel, card.dataset.qhSatellite);
    if (!satellite) continue;
    refreshSatelliteSummary(panel, card, satellite);
    for (const picker of card.querySelectorAll(".qh-override")) {
      if (!picker.hidden && refreshable(root, picker, blurred)) projectPicker(panel, picker, satellite);
    }
  }
}

export function bindQuietHours(panel) {
  const root = panel.shadowRoot;
  if (!root.__eocQuietSummaryBound) {
    root.__eocQuietSummaryBound = true;
    root.addEventListener("focusout", ({target}) => {
      if (panel._viewKey?.() !== VIEW) return;
      if (target.matches(".qh-override")) refreshSatelliteControls(panel, target);
      else if (target.id?.startsWith("qh-")) refreshScheduleControls(panel, target);
    });
    let snapshot = panel._result;
    // Page saves preserve the route DOM. Refresh resolved entity summaries when
    // the save coordinator announces dirty-state changes with a new snapshot.
    root.addEventListener("eoc-config-dirty-changed", () => {
      if (panel._viewKey?.() !== VIEW || snapshot === panel._result) return;
      snapshot = panel._result;
      refreshTimedStatus(panel);
    });
  }
  root.querySelector("#qh-enabled")?.addEventListener("change", (event) => { panel._quietHoursDraft.enabled = event.target.checked; });
  root.querySelector("#qh-start")?.addEventListener("input", (event) => { panel._quietHoursDraft.start = event.target.value; });
  root.querySelector("#qh-end")?.addEventListener("input", (event) => { panel._quietHoursDraft.end = event.target.value; });
  root.querySelector("#qh-wake")?.addEventListener("change", (event) => {
    panel._quietHoursDraft.wake_sound = event.target.value;
    refreshSatelliteControls(panel);
  });
  const volume = root.querySelector("#qh-volume");
  volume?.addEventListener("input", (event) => {
    const percent = Number(event.target.value);
    panel._quietHoursDraft.max_volume = percent / 100;
    const output = root.querySelector("#qh-volume-value");
    if (output) output.textContent = `${percent}%`;
  });
  root.querySelectorAll(".qh-override").forEach((picker) => {
    const agentId = panel._agentId;
    // Assign native properties only after upgrade: pre-upgrade own properties
    // can shadow HA's accessors even after its component has been registered.
    picker.hidden = true;
    const status = picker.ownerDocument.createElement("small");
    status.setAttribute("role", "status");
    status.textContent = "Loading entity selector…";
    picker.after(status);
    const current = () => picker.isConnected && panel._agentId === agentId && panel._viewKey?.() === VIEW;
    const initialize = () => {
      if (!current()) return;
      picker.hass = panel._hass;
      picker.includeDomains = [picker.dataset.domain];
      picker.allowCustomEntity = false;
      projectPicker(panel, picker, satelliteFor(panel, picker.dataset.satellite));
      picker.hidden = false;
      status.remove();
    };
    ensureNativePicker("ha-entity-picker", picker.ownerDocument).then(initialize).catch(() => {
      if (!current()) return;
      status.setAttribute("role", "alert");
      status.textContent = "Entity selector could not be loaded. Reload the page to try again.";
    });
    picker.addEventListener("value-changed", (event) => {
      const value = String(event?.detail?.value ?? picker.value ?? "");
      picker.value = value;
      setOverride(panel, picker.dataset.satellite, picker.dataset.kind, value);
      refreshSatelliteSummary(panel, picker.closest(".qh-satellite"), satelliteFor(panel, picker.dataset.satellite));
    });
  });

}
