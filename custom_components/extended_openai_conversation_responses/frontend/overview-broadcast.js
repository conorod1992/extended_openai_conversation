import {callMutation, captureConfigurationOwner, configurationSubmissionIsCurrent, presentAcknowledgedMutation, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";
import {acknowledgeBroadcast} from "./broadcast-acknowledgement.js";
import {broadcastAttempt, sameBroadcastPayload, retireBroadcastAttempt} from "./broadcast-retry.js";
import {watchLiveStatus, stopLiveStatus} from "./management-live-status.js";
import {captureAuthentication, authenticationReadIsCurrent} from "./authentication-presentation.js";
const ROUTE_STYLE = `/* Overview Broadcast */
#broadcast-card{background:color-mix(in srgb,var(--secondary-background-color) 20%,var(--card-background-color));border-color:color-mix(in srgb,var(--divider-color) 72%,var(--secondary-text-color))}
.broadcast-toggle-row{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:16px 0;border-bottom:1px solid var(--divider-color)}
.broadcast-toggle-row p{margin:4px 0 0;color:var(--secondary-text-color);line-height:1.45}
.status-pill{padding:5px 10px;border-radius:999px;background:var(--secondary-background-color);font-size:13px}.status-on{color:var(--success-color,#0f9d58)}
.broadcast-compose{display:grid;gap:18px;margin-top:18px}
.broadcast-message>span,.broadcast-destination legend{display:block;font-weight:600;margin-bottom:8px}
.broadcast-message textarea{box-sizing:border-box;width:100%;resize:vertical;padding:10px 12px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color);color:var(--primary-text-color);font:inherit}
.broadcast-destination{border:0;padding:0;margin:0;min-width:0}
.broadcast-mode-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.broadcast-mode-option,.broadcast-target{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color);cursor:pointer}
.broadcast-mode-option.is-selected,.broadcast-target.is-selected{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 6%,var(--card-background-color))}
.broadcast-mode-option input,.broadcast-target input{width:18px!important;height:18px!important;min-width:18px;flex:0 0 18px;margin:2px 0 0;padding:0}
.broadcast-mode-option span,.broadcast-target span{display:grid;gap:3px;min-width:0}
.broadcast-mode-option small,.broadcast-target small,.broadcast-target-heading small{color:var(--secondary-text-color);font-weight:400}
.broadcast-target-section{display:grid;gap:8px;margin-top:14px}
.broadcast-target-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}
.broadcast-targets{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px}
.broadcast-actions{justify-content:flex-end}
.broadcast-history{margin-top:24px}.broadcast-history h3{margin:0 0 8px}
.broadcast-history-item{display:grid;gap:8px;padding:12px 0;border-top:1px solid var(--divider-color)}
.broadcast-history-item>div{display:flex;justify-content:space-between;gap:14px}.broadcast-history-item small{color:var(--secondary-text-color)}
.broadcast-history-item ul{list-style:none;padding:0;margin:0;display:grid;gap:4px}.broadcast-history-item li{display:flex;justify-content:space-between;gap:12px}
@media (max-width:700px){.broadcast-mode-options{grid-template-columns:1fr}.broadcast-actions{justify-content:stretch}.broadcast-actions button{flex:1}}`;
function ensureRouteStyle(panel) {
  if (typeof document === "undefined") return;
  if (panel.shadowRoot.querySelector('style[data-eoc-feature-style="broadcast"]')) return;
  const style = document.createElement("style");
  style.dataset.eocFeatureStyle = "broadcast";
  style.textContent = ROUTE_STYLE;
  panel.shadowRoot.append(style);
}

const WS_BROADCAST = "extended_openai_conversation_responses/broadcast";

const PENDING_STATUSES = new Set(["pending", "queued", "queued_idle", "queued_busy", "waiting_idle", "delivering"]);
const broadcastWatches = new WeakMap();
// Covers the 60-minute queue TTL plus 632 seconds of announcement time.
const BROADCAST_REFRESH_LIMIT = 900;

function pendingBroadcasts(snapshot) {
  return [...(snapshot?.pending || []), ...(snapshot?.history || [])].filter(item =>
    PENDING_STATUSES.has(item.status) || Object.values(item.deliveries || {}).some(delivery => PENDING_STATUSES.has(delivery.status)));
}

export function hasPendingBroadcast(snapshot) {
  return pendingBroadcasts(snapshot).length > 0;
}

function observeBroadcasts(panel, snapshot, restartExhausted = false) {
  const pending = pendingBroadcasts(snapshot);
  if (!pending.length) {
    stopLiveStatus(panel, "broadcast");
    broadcastWatches.delete(panel);
    return false;
  }
  const ids = new Set(pending.map(item => item.id));
  const current = broadcastWatches.get(panel);
  if (!current || [...ids].some(id => !current.ids.has(id))
      || (restartExhausted && current.attempts >= BROADCAST_REFRESH_LIMIT)) {
    broadcastWatches.set(panel, {ids, attempts:0, started:Date.now()});
  } else {
    current.ids = ids;
  }
  return true;
}

export function watchBroadcastStatus(panel, snapshot, refresh) {
  if (!observeBroadcasts(panel, snapshot, true)) return;
  watchLiveStatus(panel, "broadcast", {
    view:"overview",
    delay:() => {
      const watch = broadcastWatches.get(panel);
      if (!watch || watch.attempts >= BROADCAST_REFRESH_LIMIT) return null;
      return Date.now() - watch.started < 30000 ? 1000 : 5000;
    },
    refresh:async current => {
      const watch = broadcastWatches.get(panel);
      if (!watch) return;
      watch.attempts++;
      const result = await refresh(current);
      if (result && current()) observeBroadcasts(panel, result);
    },
  });
}

function statusLabel(status) {
  return {
    pending: "Pending",
    queued_idle: "Queued",
    queued_busy: "Waiting for idle",
    waiting_idle: "Waiting for idle",
    delivering: "Delivering",
    delivered: "Delivered",
    failed: "Failed",
    expired: "Expired",
  }[status] || status;
}

function broadcastMarkup(panel, snapshot) {
  if (!snapshot) return `<p class="empty">Loading Broadcast…</p>`;
  const satellites = snapshot.catalog?.satellites || [];
  const satellitesById = new Map(satellites.map((satellite) => [satellite.id, satellite]));
  const areas = new Map((snapshot.catalog?.areas || []).map((area) => [area.id, area.name]));
  const selected = panel._broadcastSelected || new Set();
  const wholeHome = Boolean(panel._broadcastWholeHome);
  const message = panel._broadcastMessage || "";
  const enabled = snapshot.enabled === true;
  const canManage = snapshot.can_manage === true;
  const builtinVoiceCommand = snapshot.builtin_voice_command !== false;
  const active = snapshot.pending || [];
  const activeIds = new Set(active.map(item => item.id));
  const history = (snapshot.history || []).filter(item => !activeIds.has(item.id));
  const renderItems = (items) => items.map((item) => {
    const deliveries = Object.entries(item.deliveries || {}).map(([entityId, delivery]) => {
      const satellite = satellitesById.get(entityId);
      return `<li><span>${panel._e(satellite?.name || entityId)}</span><strong>${panel._e(statusLabel(delivery.status))}</strong></li>`;
    }).join("");
    return `<article class="broadcast-history-item"><div><strong>${panel._e(item.message)}</strong><small>${panel._e(new Date(item.created_at).toLocaleString())}</small></div><ul>${deliveries}</ul></article>`;
  }).join("");
  return `
    <div data-broadcast-load-error class="error" role="alert" hidden></div>
    <div class="section-heading broadcast-heading"><div><span class="section-kicker"><ha-icon icon="mdi:bullhorn-outline"></ha-icon> Home messaging</span><h2>Broadcast</h2><p>Send a spoken message to selected Assist satellites or the whole home. Busy satellites wait until they are free.</p></div></div>
    <div class="broadcast-toggle-row" data-broadcast-setting="enabled">
      <div><strong>Enable Broadcast</strong>${enabled ? "" : "<p>Broadcast is currently off.</p>"}</div>
      ${canManage ? `<label class="switch-control" for="broadcast-enabled"><input id="broadcast-enabled" type="checkbox" role="switch" aria-label="Enable Broadcast" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>` : `<strong class="status-pill ${enabled ? "status-on" : "status-off"}">${enabled ? "On" : "Off"}</strong>`}
    </div>
    <div class="broadcast-toggle-row" data-broadcast-setting="voice">
      <div><strong>Built-in voice command</strong><p>Handle ‘Broadcast [message]’ locally without AI. Turn this off to manage Broadcast commands using Request Rules or your AI model.</p></div>
      ${canManage ? `<label class="switch-control" for="broadcast-builtin-voice-command"><input id="broadcast-builtin-voice-command" type="checkbox" role="switch" aria-label="Built-in voice command" ${builtinVoiceCommand ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>` : `<strong class="status-pill ${builtinVoiceCommand ? "status-on" : "status-off"}">${builtinVoiceCommand ? "On" : "Off"}</strong>`}
    </div>
    ${enabled ? `
      <div class="broadcast-compose">
        <label class="broadcast-message"><span>Message</span><textarea id="broadcast-message" rows="3" placeholder="Dinner is ready">${panel._e(message)}</textarea></label>
        <fieldset class="broadcast-destination">
          <legend>Send to</legend>
          <div class="broadcast-mode-options" role="radiogroup" aria-label="Broadcast destination">
            <label class="broadcast-mode-option ${wholeHome ? "is-selected" : ""}"><input type="radio" name="broadcast-destination" value="whole" ${wholeHome ? "checked" : ""}><span><strong>Whole home</strong><small>Send to every available Assist satellite.</small></span></label>
            <label class="broadcast-mode-option ${wholeHome ? "" : "is-selected"}"><input type="radio" name="broadcast-destination" value="selected" ${wholeHome ? "" : "checked"}><span><strong>Selected satellites</strong><small>Choose one or more destinations below.</small></span></label>
          </div>
          ${wholeHome ? "" : `<div class="broadcast-target-section"><div class="broadcast-target-heading"><strong>Satellites</strong><small>${satellites.length} available</small></div><div class="broadcast-targets">
            ${satellites.map((sat) => {
              const area = areas.get(sat.area_id);
              const detail = [area, panel._titleCase(sat.state || "unknown")].filter(Boolean).join(" · ");
              return `<label class="broadcast-target ${selected.has(sat.id) ? "is-selected" : ""}"><input type="checkbox" data-broadcast-entity="${panel._e(sat.id)}" ${selected.has(sat.id) ? "checked" : ""}><span><strong>${panel._e(sat.name)}</strong><small>${panel._e(detail || "Assist satellite")}</small></span></label>`;
            }).join("") || `<p class="empty">No announcement-capable Assist satellites are available.</p>`}
            ${[...selected].filter(id => !satellitesById.has(id)).map(id =>
                `<label class="broadcast-target is-selected"><input type="checkbox" data-broadcast-entity="${panel._e(id)}" checked><span><strong>${panel._e(id)}</strong><small>Unavailable · Clear to remove this recipient</small></span></label>`).join("")}
          </div></div>`}
        </fieldset>
        <p id="broadcast-retry-status" role="status" ${panel._broadcastAttempt?.uncertain || panel._broadcastAttempt?.blocked ? "" : "hidden"}>An earlier send may already have been queued. Retry unchanged while its original ten-minute window is available. Sending as new can repeat an announcement. Recent history is cleared on restart, and the earlier announcement may already have played.</p>
        <div class="actions broadcast-actions"><button id="broadcast-refresh" type="button" class="secondary compact-button">Refresh devices</button><button id="broadcast-send" type="button" ${!satellites.length || panel._broadcastSending ? "disabled" : ""}>Send broadcast</button></div>
      </div>` : '<button id="broadcast-refresh" type="button" class="secondary compact-button">Refresh Broadcast</button>'}
    <div class="broadcast-history">
      ${active.length ? `<section aria-label="Pending broadcasts"><h3>Pending broadcasts</h3>${renderItems(active)}</section>` : ""}
      <section aria-label="Recent broadcasts"><h3>Recent broadcasts</h3>
        ${history.length ? renderItems(history.slice(0, 10)) : `<p class="empty">No completed broadcasts yet.</p>`}
      </section>
    </div>`;
}

function bindBroadcastControls(panel, snapshot) {
  const root = panel.shadowRoot;
  for (const [selector, action, field, label] of [
    ["#broadcast-enabled", "set_enabled", "enabled", "Broadcast"],
    ["#broadcast-builtin-voice-command", "set_builtin_voice_command", "builtin_voice_command", "Built-in voice command"],
  ]) root.querySelector(selector)?.addEventListener("change", async (event) => {
    const control = event.target;
    const enabled = control.checked;
    control.disabled = true;
    const submission = captureConfigurationOwner(panel);
    const authenticated = captureAuthentication(panel);
    const current = () => authenticated() && configurationSubmissionIsCurrent(panel, submission)
      && panel.isConnected !== false && panel._viewKey?.() === "overview";
    try {
      const result = await callMutation(() => panel._hass.callWS({type: WS_BROADCAST, action, enabled}));
      requireMutationAcknowledgement(typeof result?.[field] === "boolean", `changing ${label}`);
      await presentAcknowledgedMutation(panel, current, `${label} ${result[field] ? "enabled" : "disabled"}`, async () => {
        if (!await loadBroadcast(panel, current)) throw Error("Broadcast status could not be refreshed.");
      }, result);
    } catch (err) {
      if (!current()) return;
      panel._toast(`Unable to change ${label}: ${err.message || String(err)}`, true);
      await loadBroadcast(panel, current);
    } finally {
      // Authentication can retire the acknowledgement while retaining this
      // owner's controls. Release only this operation's original input.
      control.disabled = false;
    }
  });
  root.querySelector("#broadcast-message")?.addEventListener("input", (event) => { panel._broadcastMessage = event.target.value; panel._broadcastDraftRevision = (panel._broadcastDraftRevision || 0) + 1; });
  root.querySelectorAll('input[name="broadcast-destination"]').forEach((input) => input.addEventListener("change", (event) => {
    if (!event.target.checked) return;
    panel._broadcastWholeHome = event.target.value === "whole";
    panel._broadcastDraftRevision = (panel._broadcastDraftRevision || 0) + 1;
    const host = root.querySelector("#broadcast-card");
    const latest = panel._eocBroadcastSnapshot || snapshot;
    if (host) {
      const loadError = root.querySelector("[data-broadcast-load-error]");
      renderBroadcastCard(panel, host, latest);
      if (loadError && !loadError.hidden) root.querySelector("[data-broadcast-load-error]")?.replaceWith(loadError);
    }
  }));
  bindBroadcastTargets(panel, root);
  root.querySelector("#broadcast-refresh")?.addEventListener("click", () => loadBroadcast(panel));
  root.querySelector("#broadcast-send")?.addEventListener("click", async (event) => {
    if (panel._broadcastSending) return;
    const button = event.currentTarget;
    const draftRevision = panel._broadcastDraftRevision || 0;
    const submission = captureConfigurationOwner(panel);
    const authenticated = captureAuthentication(panel);
    const message = String(panel._broadcastMessage || "").trim();
    const selected = [...(panel._broadcastSelected || new Set())];
    const wholeHome = Boolean(panel._broadcastWholeHome);
    if (!message) return panel._toast("Enter a message to broadcast.", true);
    if (!wholeHome && !selected.length) return panel._toast("Choose at least one Assist satellite or Whole home.", true);
    panel._broadcastSending = true;
    button.disabled = true;
    let sending = true;
    const release = () => {
      if (!sending) return;
      sending = false;
      panel._broadcastSending = false;
      const notice = panel.shadowRoot.querySelector("#broadcast-retry-status");
      if (notice) notice.hidden = !(panel._broadcastAttempt?.uncertain || panel._broadcastAttempt?.blocked);
      const currentButton = panel.shadowRoot.querySelector("#broadcast-send");
      if (currentButton) currentButton.disabled = !(panel._eocBroadcastSnapshot?.catalog?.satellites || []).length;
    };
    let attempt;
    let retrying = false;
    let dispatched = false;
    try {
      const payload = {message, whole_home:wholeHome, entity_ids:wholeHome ? [] : selected};
      if (panel._broadcastAttempt) attempt = broadcastAttempt(panel, submission, payload, null);
      const replace = !attempt || attempt.blocked || !sameBroadcastPayload(attempt.payload, payload);
      const ownsSubmission = () => {
        return authenticated()
          && (panel._broadcastDraftRevision || 0) === draftRevision
          && (panel._broadcastAttempt || null) === (attempt || null)
          && panel.isConnected !== false && panel._viewKey?.() === "overview";
      };
      if (attempt && replace) {
        if (!await panel._confirm("Send a new Broadcast?", "The previous send may already have been queued or delivered. Check recent broadcasts before sending again. A new send can repeat the announcement.", "Send as new")) return;
        // Confirmation yields. Never send later edits or a different user's work.
        if (!ownsSubmission()) return;
      }
      if (replace) {
        // A page may be idle for longer than the retry window. Fresh intent gets
        // server-clock freshness now; uncertain retries retain their old token.
        // Read without replacing the composer or treating read failure as a send.
        const fresh = await panel._hass.callWS({type:WS_BROADCAST, action:"snapshot"});
        if (!ownsSubmission()) return;
        if (typeof fresh?.retry_token !== "string" || !fresh.retry_token) throw Error("Broadcast could not be prepared. Refresh devices before sending.");
        if (!wholeHome && Array.isArray(fresh.catalog?.satellites)) {
          const available = new Set(fresh.catalog.satellites.map(satellite => satellite.id));
          if (selected.some(id => !available.has(id))) {
            applyBroadcastSnapshot(panel, fresh);
            throw Error("A selected satellite is unavailable. Review the recipients before sending.");
          }
        }
        retireBroadcastAttempt(panel, attempt);
        attempt = broadcastAttempt(panel, submission, payload, fresh.retry_token);
      }
      retrying = attempt.uncertain;
      attempt.uncertain = true;
      dispatched = true;
      const response = await callMutation(() => panel._hass.callWS({
        type: WS_BROADCAST,
        action: "send",
        ...attempt.payload,
        ...(attempt.operationId ? {operation_id:attempt.operationId} : {}),
      }));
      const result = acknowledgeBroadcast(panel, submission, message, draftRevision, response);
      // A stale authentication cannot clear its retained draft. Keep new-send
      // confirmation coupled to it rather than silently rearming that message.
      if (authenticated()) retireBroadcastAttempt(panel, attempt);
      else attempt.blocked = true;
      release();
      if (await presentAcknowledgedMutation(panel, submission, "Broadcast queued", null, result)) {
        const visit = panel._loadToken;
        const current = () => authenticated() && panel.isConnected !== false
          && panel._viewKey() === "overview" && panel._loadToken === visit;
        if (current() && !await loadBroadcast(panel, current, {afterSend:true}) && current()) {
          panel._toast("Broadcast queued, but history could not be refreshed.", true);
        }
      }
    } catch (err) {
      if (attempt && dispatched) {
        if (["invalid_request", "broadcast_retry_capacity"].includes(err.code) || err.mutationOutcome === "rejected") {
          if (retrying) attempt.blocked = true;
          else retireBroadcastAttempt(panel, attempt);
        }
        else if (!attempt.operationId || ["broadcast_retry_expired", "broadcast_retry_uncertain"].includes(err.code)) attempt.blocked = true;
      }
      panel._toast(`Unable to send Broadcast: ${err.message || String(err)}`, true);
    } finally {
      release();
    }
  });
}

function bindBroadcastTargets(panel, root) {
  root.querySelectorAll("[data-broadcast-entity]").forEach((box) => box.addEventListener("change", (event) => {
    panel._broadcastSelected ||= new Set();
    if (event.target.checked) panel._broadcastSelected.add(event.target.dataset.broadcastEntity);
    else panel._broadcastSelected.delete(event.target.dataset.broadcastEntity);
    panel._broadcastDraftRevision = (panel._broadcastDraftRevision || 0) + 1;
    event.target.closest(".broadcast-target")?.classList.toggle("is-selected", event.target.checked);
  }));
}

function preserveBroadcastFocus(panel, host) {
  const active = panel.shadowRoot.activeElement;
  if (!active || !host.contains(active)) return () => {};
  const {id, name, value, selectionStart, selectionEnd, selectionDirection} = active;
  const satellite = active.dataset.broadcastEntity;
  return () => {
    if (host.contains(active)) return;
    const next = id ? panel.shadowRoot.getElementById(id)
      : [...host.querySelectorAll("input")].find(input => satellite
        ? input.dataset.broadcastEntity === satellite
        : input.name === name && input.value === value);
    const focus = next || host.querySelector("#broadcast-enabled, #broadcast-refresh");
    focus?.focus({preventScroll: true});
    if (next && selectionStart != null) next.setSelectionRange(selectionStart, selectionEnd, selectionDirection);
  };
}

function renderBroadcastCard(panel, host, snapshot) {
  const restoreFocus = preserveBroadcastFocus(panel, host);
  host.innerHTML = broadcastMarkup(panel, snapshot);
  bindBroadcastControls(panel, snapshot);
  restoreFocus();
}

function reconcileBroadcastPoll(panel, host, previous, result) {
  if (previous.enabled !== result.enabled || previous.can_manage !== result.can_manage) {
    renderBroadcastCard(panel, host, result);
    return;
  }
  const template = host.ownerDocument.createElement("template");
  template.innerHTML = broadcastMarkup(panel, result);
  const content = template.content;
  const replace = selector => {
    const fresh = content.querySelector(selector);
    if (fresh) host.querySelector(selector)?.replaceWith(fresh);
    return fresh;
  };
  replace(".broadcast-history");
  if (previous.builtin_voice_command !== result.builtin_voice_command) {
    const toggle = host.querySelector("#broadcast-builtin-voice-command");
    if (toggle) {
      if (!toggle.disabled) toggle.checked = result.builtin_voice_command !== false;
    } else replace('[data-broadcast-setting="voice"] .status-pill');
  }
  if (JSON.stringify(previous.catalog) !== JSON.stringify(result.catalog)) {
    const restoreFocus = preserveBroadcastFocus(panel, host);
    const targets = replace(".broadcast-target-section");
    if (targets) bindBroadcastTargets(panel, targets);
    const send = host.querySelector("#broadcast-send");
    if (send) send.disabled = !(result.catalog?.satellites || []).length || Boolean(panel._broadcastSending);
    restoreFocus();
  }
}

function applyBroadcastSnapshot(panel, snapshot, {preserveControls = false} = {}) {
  if (panel._viewKey?.() !== "overview") return;
  const host = panel.shadowRoot.querySelector("#broadcast-card");
  if (!host) return;
  const previous = panel._eocBroadcastSnapshotIsCurrent?.() && panel._eocBroadcastSnapshot;
  if (preserveControls && previous && host.querySelector?.(".broadcast-history")) {
    reconcileBroadcastPoll(panel, host, previous, snapshot);
  } else renderBroadcastCard(panel, host, snapshot);
  panel._eocBroadcastEpoch = (panel._eocBroadcastEpoch || 0) + 1;
  panel._eocBroadcastSnapshot = snapshot;
  panel._eocBroadcastSnapshotVisit = panel._loadToken;
  panel._eocBroadcastSnapshotIsCurrent = captureAuthentication(panel);
  syncBroadcastWatch(panel, snapshot);
}

function syncBroadcastWatch(panel, snapshot) {
  watchBroadcastStatus(panel, snapshot, async current => {
    const epoch = panel._eocBroadcastEpoch;
    const token = panel._eocBroadcastReadToken = (panel._eocBroadcastReadToken || 0) + 1;
    const result = await panel._hass.callWS({type:WS_BROADCAST, action:"snapshot"});
    if (!current() || panel._eocBroadcastEpoch !== epoch || panel._eocBroadcastReadToken !== token) return;
    const previous = panel._eocBroadcastSnapshot;
    panel._eocBroadcastSnapshot = result;
    const currentHost = panel.shadowRoot.querySelector("#broadcast-card");
    if (!currentHost) return;
    reconcileBroadcastPoll(panel, currentHost, previous, result);
    return result;
  });
}

function applyBroadcastError(panel, err) {
  if (panel._viewKey?.() !== "overview") return;
  const host = panel.shadowRoot.querySelector("#broadcast-card");
  if (!host) return;
  const snapshot = panel._eocBroadcastSnapshotIsCurrent?.() && panel._eocBroadcastSnapshot;
  if (!host.querySelector("[data-broadcast-load-error]") && snapshot) {
    host.innerHTML = broadcastMarkup(panel, snapshot);
    bindBroadcastControls(panel, snapshot);
  }
  let notice = host.querySelector("[data-broadcast-load-error]");
  if (!notice) {
    host.innerHTML = '<div data-broadcast-load-error class="error" role="alert"></div><button id="broadcast-refresh" class="secondary" type="button">Refresh Broadcast</button>';
    notice = host.querySelector("[data-broadcast-load-error]");
    host.querySelector("#broadcast-refresh").addEventListener("click", () => loadBroadcast(panel));
  }
  notice.textContent = `Unable to load Broadcast: ${err.message || String(err)}`;
  notice.hidden = false;
}

async function loadBroadcast(panel, owns = () => true, {afterSend = false} = {}) {
  const current = captureAuthentication(panel);
  const token = panel._eocBroadcastReadToken = (panel._eocBroadcastReadToken || 0) + 1;
  try {
    const snapshot = await panel._hass.callWS({type: WS_BROADCAST, action: "snapshot"});
    if (current() && owns() && panel._eocBroadcastReadToken === token) applyBroadcastSnapshot(panel, snapshot, {preserveControls:afterSend});
    return true;
  } catch (err) {
    if (current() && owns() && panel._eocBroadcastReadToken === token) {
      if (!afterSend) applyBroadcastError(panel, err);
    }
    return false;
  }
}

export function bindBroadcast(panel, broadcastPromise, owns = () => true) {
  const authCurrent = captureAuthentication(panel);
  ensureRouteStyle(panel);
  stopLiveStatus(panel, "broadcast");
  const token = panel._eocBroadcastReadToken = (panel._eocBroadcastReadToken || 0) + 1;
  const current = () => owns() && authCurrent() && authenticationReadIsCurrent(broadcastPromise) && panel._eocBroadcastReadToken === token;
  return Promise.resolve(broadcastPromise)
    .then((snapshot) => { if (current()) applyBroadcastSnapshot(panel, snapshot, {preserveControls:true}); })
    .catch((err) => {
      if (!current()) return;
      const host = panel.shadowRoot.querySelector("#broadcast-card");
      if (host?.querySelector?.(".broadcast-history")) syncBroadcastWatch(panel, panel._eocBroadcastSnapshot);
      else applyBroadcastError(panel, err);
    });
}
