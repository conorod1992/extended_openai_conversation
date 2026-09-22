const WS_BROADCAST = "extended_openai_conversation_responses/broadcast";

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
  const history = snapshot.history || [];
  return `
    <div class="section-heading broadcast-heading"><div><span class="section-kicker"><ha-icon icon="mdi:bullhorn-outline"></ha-icon> Home messaging</span><h2>Broadcast</h2><p>Send a spoken message to selected Assist satellites or the whole home. Busy satellites wait until they are free.</p></div></div>
    <div class="broadcast-toggle-row">
      <div><strong>Enable Broadcast</strong>${enabled ? "" : "<p>Broadcast is currently off.</p>"}</div>
      ${canManage ? `<label class="switch-control" for="broadcast-enabled"><input id="broadcast-enabled" type="checkbox" role="switch" aria-label="Enable Broadcast" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>` : `<strong class="status-pill ${enabled ? "status-on" : "status-off"}">${enabled ? "On" : "Off"}</strong>`}
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
          </div></div>`}
        </fieldset>
        <div class="actions broadcast-actions"><button id="broadcast-refresh" type="button" class="secondary compact-button">Refresh devices</button><button id="broadcast-send" type="button" ${!satellites.length ? "disabled" : ""}>Send broadcast</button></div>
      </div>` : ""}
    <div class="broadcast-history">
      <h3>Recent broadcasts</h3>
      ${history.length ? history.slice(0, 10).map((item) => {
        const deliveries = Object.entries(item.deliveries || {}).map(([entityId, delivery]) => {
          const satellite = satellitesById.get(entityId);
          return `<li><span>${panel._e(satellite?.name || entityId)}</span><strong>${panel._e(statusLabel(delivery.status))}</strong></li>`;
        }).join("");
        return `<article class="broadcast-history-item"><div><strong>${panel._e(item.message)}</strong><small>${panel._e(new Date(item.created_at).toLocaleString())}</small></div><ul>${deliveries}</ul></article>`;
      }).join("") : `<p class="empty">No broadcasts sent yet.</p>`}
    </div>`;
}

function bindBroadcastControls(panel, snapshot) {
  const root = panel.shadowRoot;
  root.querySelector("#broadcast-enabled")?.addEventListener("change", async (event) => {
    const enabled = event.target.checked;
    event.target.disabled = true;
    try {
      await panel._hass.callWS({type: WS_BROADCAST, action: "set_enabled", enabled});
      await loadBroadcast(panel);
      panel._toast(`Broadcast ${enabled ? "enabled" : "disabled"}`);
    } catch (err) {
      panel._toast(`Unable to change Broadcast: ${err.message || String(err)}`, true);
      await loadBroadcast(panel);
    }
  });
  root.querySelector("#broadcast-message")?.addEventListener("input", (event) => { panel._broadcastMessage = event.target.value; });
  root.querySelectorAll('input[name="broadcast-destination"]').forEach((input) => input.addEventListener("change", (event) => {
    if (!event.target.checked) return;
    panel._broadcastWholeHome = event.target.value === "whole";
    const host = root.querySelector("#broadcast-card");
    if (host) host.innerHTML = broadcastMarkup(panel, snapshot);
    bindBroadcastControls(panel, snapshot);
  }));
  root.querySelectorAll("[data-broadcast-entity]").forEach((box) => box.addEventListener("change", (event) => {
    panel._broadcastSelected ||= new Set();
    if (event.target.checked) panel._broadcastSelected.add(event.target.dataset.broadcastEntity);
    else panel._broadcastSelected.delete(event.target.dataset.broadcastEntity);
    event.target.closest(".broadcast-target")?.classList.toggle("is-selected", event.target.checked);
  }));
  root.querySelector("#broadcast-refresh")?.addEventListener("click", () => loadBroadcast(panel));
  root.querySelector("#broadcast-send")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const message = String(panel._broadcastMessage || "").trim();
    const selected = [...(panel._broadcastSelected || new Set())];
    const wholeHome = Boolean(panel._broadcastWholeHome);
    if (!message) return panel._toast("Enter a message to broadcast.", true);
    if (!wholeHome && !selected.length) return panel._toast("Choose at least one Assist satellite or Whole home.", true);
    button.disabled = true;
    try {
      await panel._hass.callWS({
        type: WS_BROADCAST,
        action: "send",
        message,
        whole_home: wholeHome,
        entity_ids: selected,
      });
      panel._broadcastMessage = "";
      await loadBroadcast(panel);
      panel._toast("Broadcast queued");
    } catch (err) {
      panel._toast(`Unable to send Broadcast: ${err.message || String(err)}`, true);
      button.disabled = false;
    }
  });
}

function applyBroadcastSnapshot(panel, snapshot) {
  if (panel._viewKey?.() !== "overview") return;
  const host = panel.shadowRoot.querySelector("#broadcast-card");
  if (!host) return;
  const available = new Set((snapshot.catalog?.satellites || []).map((satellite) => satellite.id));
  panel._broadcastSelected = new Set([...(panel._broadcastSelected || new Set())].filter((entityId) => available.has(entityId)));
  host.innerHTML = broadcastMarkup(panel, snapshot);
  bindBroadcastControls(panel, snapshot);
}

function applyBroadcastError(panel, err) {
  if (panel._viewKey?.() !== "overview") return;
  const host = panel.shadowRoot.querySelector("#broadcast-card");
  if (!host) return;
  host.innerHTML = `<div class="error" role="alert">Unable to load Broadcast: ${panel._e(err.message || String(err))}</div>`;
}

async function loadBroadcast(panel) {
  try {
    const snapshot = await panel._hass.callWS({type: WS_BROADCAST, action: "snapshot"});
    applyBroadcastSnapshot(panel, snapshot);
  } catch (err) {
    applyBroadcastError(panel, err);
  }
}

export function bindBroadcast(panel, broadcastPromise) {
  return Promise.resolve(broadcastPromise)
    .then((snapshot) => applyBroadcastSnapshot(panel, snapshot))
    .catch((err) => applyBroadcastError(panel, err))
    .finally(() => {
      if (panel._eocOverviewBroadcastPromise === broadcastPromise) panel._eocOverviewBroadcastPromise = null;
    });
}
