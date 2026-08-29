const WS_BROADCAST = "extended_openai_conversation_responses/broadcast";

function card(panel, title, status, detail, page, section, action) {
  return `<article class="dashboard-card"><div><h2>${panel._e(title)}</h2><strong>${panel._e(String(status))}</strong><p>${panel._e(detail)}</p></div><button type="button" class="secondary dashboard-action" data-page="${page}" data-subsection="${section}">${action}</button></article>`;
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
  const areas = new Map((snapshot.catalog?.areas || []).map((area) => [area.id, area.name]));
  const selected = panel._broadcastSelected || new Set();
  const wholeHome = Boolean(panel._broadcastWholeHome);
  const message = panel._broadcastMessage || "";
  const enabled = snapshot.enabled === true;
  const canManage = snapshot.can_manage === true;
  const history = snapshot.history || [];
  return `
    <div class="section-heading"><div><h2>Broadcast</h2><p>Send a one-way spoken message to selected Assist satellites or the whole home. Busy satellites wait until they are idle instead of interrupting an active voice session.</p></div></div>
    <div class="broadcast-toggle-row">
      <div><strong>Enable Broadcast</strong><p>${enabled ? "Broadcast is available to voice requests, Function Tools, automations, and this page." : "Broadcast is off. No Extended OpenAI broadcast will be sent until an administrator enables it."}</p></div>
      ${canManage ? `<label class="switch-control" for="broadcast-enabled"><input id="broadcast-enabled" type="checkbox" role="switch" aria-label="Enable Broadcast" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>` : `<strong>${enabled ? "On" : "Off"}</strong>`}
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
          const satellite = satellites.find((sat) => sat.id === entityId);
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

async function loadBroadcast(panel) {
  const host = panel.shadowRoot.querySelector("#broadcast-card");
  if (!host) return;
  try {
    const snapshot = await panel._hass.callWS({type: WS_BROADCAST, action: "snapshot"});
    if (!panel.shadowRoot.querySelector("#broadcast-card")) return;
    const available = new Set((snapshot.catalog?.satellites || []).map((satellite) => satellite.id));
    panel._broadcastSelected = new Set([...(panel._broadcastSelected || new Set())].filter((entityId) => available.has(entityId)));
    host.innerHTML = broadcastMarkup(panel, snapshot);
    bindBroadcastControls(panel, snapshot);
  } catch (err) {
    host.innerHTML = `<div class="error" role="alert">Unable to load Broadcast: ${panel._e(err.message || String(err))}</div>`;
  }
}

export function renderOverview(panel, agent) {
  const result = panel._result || {};
  const usage = result.usage || {};
  const conversations = result.conversations || {};
  const guest = agent.guest_mode || {};
  const warnings = [];
  if (["active", "active_indefinitely"].includes(guest.state) && !guest.has_home_assistant_exclusions) warnings.push("Guest Mode is active without configured Home Assistant exclusions.");
  for (const issue of result.load_errors || []) warnings.push(`${issue.label} could not be loaded. Other overview information is still available.`);
  const memoryCount = Number(agent.memory_count || 0).toLocaleString();
  const knowledgeCount = Number(agent.knowledge_source_count || 0).toLocaleString();
  return `<style>
      .broadcast-toggle-row{display:flex;justify-content:space-between;gap:18px;align-items:center;padding:14px 0;border-bottom:1px solid var(--divider-color)}
      .broadcast-toggle-row p{margin:4px 0 0;color:var(--secondary-text-color);line-height:1.45}
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
      @media (max-width:700px){.broadcast-mode-options{grid-template-columns:1fr}.broadcast-actions{justify-content:stretch}.broadcast-actions button{flex:1}}
    </style>
    <section class="page-intro"><h1>${panel._e(agent.title)}</h1><p>Your assistant at a glance. Open a card to change or inspect that area.</p></section>
    ${warnings.length ? `<section class="overview-warnings" aria-label="Actionable warnings">${warnings.map((warning) => `<div class="notice"><strong>Review recommended</strong><p>${panel._e(warning)}</p></div>`).join("")}</section>` : ""}
    <section class="dashboard-grid" aria-label="Assistant overview">
      ${card(panel,"Assistant",`${agent.provider} · ${agent.model}`,"Model, responses, conversation behavior, prompt, and voice.","assistant","basics","Configure")}
      ${card(panel,"Capabilities",`${Number(agent.function_count || 0).toLocaleString()} functions · ${Number(agent.function_group_count || 0).toLocaleString()} groups`,"Home Assistant access, custom functions, and visitor restrictions.","capabilities","home-assistant","Manage")}
      ${card(panel,"Memory & Knowledge",panel._titleCase(agent.memory_mode),`${memoryCount} memories · ${knowledgeCount} Knowledge sources`,"data-memory","memories","Manage")}
      ${card(panel,"Conversation history",agent.archive_enabled ? "Archive enabled" : "Archive disabled",result.load_errors?.some((issue) => issue.key === "conversations") ? "Retention unavailable" : `Retention: ${conversations.archive_retention_days || 30} days`,"data-memory","conversations","View")}
      ${card(panel,"Guest Mode",panel._titleCase(String(guest.state || "inactive").replaceAll("_"," ")),"Integration-enforced visitor access and data restrictions.","capabilities","guest-mode","Configure")}
      ${card(panel,"Usage",result.load_errors?.some((issue) => issue.key === "usage") ? "Usage unavailable" : `${Number(usage.today?.total_tokens || 0).toLocaleString()} tokens today`,result.load_errors?.some((issue) => issue.key === "usage") ? "Usage summary could not be loaded." : `${Number(usage.month?.total_tokens || 0).toLocaleString()} this month`,"usage-maintenance","usage","View")}
    </section>
    <section id="broadcast-card" class="content-card" aria-label="Broadcast">${broadcastMarkup(panel, null)}</section>`;
}

export function bindOverview(panel) {
  panel.shadowRoot.querySelectorAll(".dashboard-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
  loadBroadcast(panel);
}
