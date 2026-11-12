import {buildSetupHealth} from "./overview-health.js";

function card(panel, title, status, detail, page, section, action, icon, tone = "neutral") {
  return `<article class="dashboard-card dashboard-tone-${tone}"><div class="dashboard-card-main"><span class="dashboard-icon" aria-hidden="true"><ha-icon icon="${panel._e(icon)}"></ha-icon></span><div><h3>${panel._e(title)}</h3><strong>${panel._e(String(status))}</strong><p>${panel._e(detail)}</p></div></div><button type="button" class="secondary dashboard-action" data-page="${page}" data-subsection="${section}">${action}</button></article>`;
}

const HEALTH_ICONS = {
  ready: "mdi:check-circle-outline",
  warning: "mdi:alert-circle-outline",
  error: "mdi:close-circle-outline",
  neutral: "mdi:minus-circle-outline",
  unknown: "mdi:help-circle-outline",
};

function healthAction(panel, health, check) {
  if (!health.can_manage || !check.action) return "";
  const label = {
    provider_runtime: "Run live test",
    model_lifecycle: "Change model",
    instructions: "Review",
    home_assistant_exposure: "Review",
    memory: "Configure",
    knowledge: "Manage",
    web_search: check.state === "warning" ? "Fix" : "Configure",
  }[check.id] || "Review";
  return `<button type="button" class="secondary setup-health-action" data-page="${panel._e(check.action.page || "")}" data-subsection="${panel._e(check.action.subsection || "")}" data-target="${panel._e(check.action.target || "")}">${panel._e(label)}</button>`;
}

function setupHealthMarkup(panel, health, cards) {
  if (!health?.checks?.length) return "";
  const state = ["ready", "warning", "error"].includes(health.state) ? health.state : "ready";
  const errors = Number(health.error_count || 0);
  const warnings = Number(health.warning_count || 0);
  const unknown = Number(health.unknown_count || 0);
  const issues = errors + warnings;
  const summaryTitle = errors
    ? "Needs attention"
    : warnings
      ? "Review recommended"
      : unknown
        ? "Status incomplete"
        : "Ready";
  const summaryDetail = issues && unknown
    ? `${issues} ${issues === 1 ? "issue" : "issues"} to review · ${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`
    : issues
      ? `${issues} ${issues === 1 ? "issue" : "issues"} to review`
      : unknown
        ? `${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`
        : "Core setup looks ready";
  return `<section class="content-card configuration-health setup-health setup-health-${state}" aria-label="Configuration and health">
    <div class="setup-health-heading">
      <div><h2>Configuration & health</h2><p>Open an area to configure or inspect it.</p></div>
      <div class="setup-health-summary" role="status"><strong>${panel._e(summaryTitle)}</strong><span>${panel._e(summaryDetail)}</span></div>
    </div>
    ${health.checks.some(check => ["error", "warning"].includes(check.state)) ? `<ul class="configuration-health-issues" aria-label="Health issues">${health.checks.filter(check => ["error", "warning"].includes(check.state)).map(check => `<li><strong>${panel._e(check.title)}:</strong> ${panel._e(check.value)}${healthAction(panel, health, check)}</li>`).join("")}</ul>` : ""}
    ${cards}
    <details class="eoc-details-base" id="overview-health-details"><summary>Detailed health checks</summary>
    <div class="setup-health-grid">
      ${health.checks.map((check) => {
        const checkState = HEALTH_ICONS[check.state] ? check.state : "unknown";
        const actionMarkup = ["error", "warning"].includes(check.state) ? "" : healthAction(panel, health, check);
        return `<article class="setup-health-check setup-health-check-${checkState}"><ha-icon class="setup-health-icon" icon="${HEALTH_ICONS[checkState]}" aria-hidden="true"></ha-icon><div class="setup-health-copy"><span>${panel._e(check.title)}</span><strong>${panel._e(check.value)}</strong><p>${panel._e(check.detail)}</p></div>${actionMarkup}</article>`;
      }).join("")}
    </div>
    <p class="setup-health-footnote"><ha-icon icon="mdi:information-outline" aria-hidden="true"></ha-icon> Connection tests only run when you start one from Diagnostics.</p>
    </details>
  </section>`;
}

export function renderOverview(panel, agent) {
  const result = panel._result || {};
  const usage = result.usage || {};
  const conversations = result.conversations || {};
  const setupHealth = buildSetupHealth(result.setup_health || {});
  const checks = Object.fromEntries(setupHealth.checks.map(check => [check.id, check]));
  const web = checks.web_search;
  const guest = agent.guest_mode || {};
  const warnings = [];
  if (["active", "active_indefinitely"].includes(guest.state) && guest.has_home_assistant_exclusions === false) warnings.push("Guest Mode is active without configured Home Assistant exclusions.");
  for (const issue of new Map([...(result.load_errors || []), ...(agent.metadata_errors || [])].map((item) => [item.key, item])).values()) warnings.push(`${issue.label} could not be loaded. Other overview information is still available.`);
  const loading = result.loading || {};
  const memoryCount = agent.memory_count == null ? "Unknown" : Number(agent.memory_count).toLocaleString();
  const knowledgeCount = agent.knowledge_source_count == null ? "Unknown" : Number(agent.knowledge_source_count).toLocaleString();
  const archiveTone = agent.archive_enabled ? "positive" : "neutral";
  const guestTone = ["active", "active_indefinitely", "scheduled"].includes(guest.state) ? "warning" : "neutral";
  return `<style>
      .configuration-health .dashboard-grid{grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:12px}
      .configuration-health .dashboard-card{display:flex;flex-direction:column;align-items:stretch;gap:12px;padding:16px;background:var(--secondary-background-color);box-shadow:none}
      .configuration-health .dashboard-card-main{display:flex;align-items:start;gap:12px;min-width:0}
      .configuration-health .dashboard-icon{display:grid;place-items:center;flex:0 0 32px;width:32px;height:32px;color:var(--eoc-accent-text)}
      .configuration-health .dashboard-icon ha-icon{--mdc-icon-size:23px}
      .configuration-health .dashboard-card h3{margin:0;font-size:15px;font-weight:600}.configuration-health .dashboard-card strong{margin-top:6px;font-size:14px;line-height:1.4}.configuration-health .dashboard-card p{margin:5px 0 0;font-size:13px;line-height:1.45}
      .configuration-health .dashboard-action{align-self:start;min-height:34px;padding:5px 12px;margin-top:auto}
      .configuration-health .dashboard-tone-warning .dashboard-icon{color:var(--warning-color,#b26a00)}.configuration-health .dashboard-tone-positive .dashboard-icon{color:var(--success-color,#0f9d58)}
      .configuration-health-issues{padding:12px 12px 12px 30px;border-left:4px solid var(--warning-color);background:var(--secondary-background-color)}.configuration-health-issues li{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;margin:5px 0}.configuration-health-issues .setup-health-action{margin-left:auto}
      #overview-health-details{margin-top:20px}#overview-health-details>summary{font-weight:600}#overview-health-details .setup-health-grid{margin-top:12px}
      .section-kicker{display:flex;align-items:center;gap:7px;margin-bottom:7px;color:var(--eoc-accent-text);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.045em}
      .section-kicker ha-icon{--mdc-icon-size:18px}
      .setup-health{margin:0 0 26px;padding:22px;border:1px solid var(--divider-color);border-radius:14px;background:color-mix(in srgb,var(--secondary-background-color) 22%,var(--card-background-color))}
      .setup-health-ready{border-left:4px solid var(--success-color,#0f9d58)}.setup-health-warning{border-left:4px solid var(--warning-color,#b26a00)}.setup-health-error{border-left:4px solid var(--error-color,#db4437)}
      .setup-health-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:22px;margin-bottom:16px}.setup-health-heading h2{margin:0;font-size:19px}.setup-health-heading p{margin:5px 0 0;color:var(--secondary-text-color);line-height:1.45}
      .setup-health-summary{display:grid;gap:2px;flex:0 0 auto;min-width:150px;padding:10px 13px;border-radius:10px;background:var(--card-background-color);border:1px solid var(--divider-color)}.setup-health-summary strong{font-size:15px}.setup-health-summary span{font-size:12px;color:var(--secondary-text-color)}
      .setup-health-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.setup-health-check{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:11px;align-items:start;padding:13px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color)}
      .setup-health-icon{margin-top:2px;--mdc-icon-size:21px;color:var(--secondary-text-color)}.setup-health-check-ready .setup-health-icon{color:var(--success-color,#0f9d58)}.setup-health-check-warning .setup-health-icon{color:var(--warning-color,#b26a00)}.setup-health-check-error .setup-health-icon{color:var(--error-color,#db4437)}
      .setup-health-copy{display:grid;gap:3px;min-width:0}.setup-health-copy>span{font-size:12px;color:var(--secondary-text-color)}.setup-health-copy>strong{font-size:15px;line-height:1.35}.setup-health-copy p{margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.4}.setup-health-action{align-self:center;min-height:34px;padding:5px 10px;white-space:nowrap}
      .setup-health-footnote{display:flex;align-items:flex-start;gap:7px;margin:13px 0 0;color:var(--secondary-text-color);font-size:12px;line-height:1.45}.setup-health-footnote ha-icon{flex:0 0 auto;--mdc-icon-size:17px}
      @media (max-width:800px){.setup-health-grid{grid-template-columns:1fr}.setup-health-heading{display:grid}.setup-health-summary{min-width:0}.setup-health-check{grid-template-columns:auto minmax(0,1fr)}.setup-health-action{grid-column:2;justify-self:start}}
    </style>
    <section class="page-intro"><h1>${panel._e(agent.title)}</h1><p>Review your assistant’s settings and current status.</p></section>
    ${setupHealthMarkup(panel, setupHealth, `<section class="dashboard-grid" aria-label="Assistant overview">
      ${card(panel,"Assistant",`${agent.provider} · ${agent.model}`,`${checks.provider_runtime?.state === "error" ? "Connection unavailable" : checks.provider_runtime?.value ? "Connection available" : "Connection status unavailable"} · ${checks.instructions?.value || "Instructions unavailable"}`,"assistant","basics","Configure","mdi:robot-outline")}
      ${card(panel,"Capabilities",`${agent.function_count == null ? "Loading…" : `${Number(agent.function_count).toLocaleString()} function${agent.function_count === 1 ? "" : "s"}`} · ${agent.function_group_count == null ? "Unknown groups" : `${Number(agent.function_group_count).toLocaleString()} group${agent.function_group_count === 1 ? "" : "s"}`}`,`${checks.home_assistant_exposure?.value || "Assist exposure unavailable"}${checks.function_tools?.state === "warning" || checks.function_tools?.state === "error" ? ` · ${checks.function_tools.value}` : ""}`,"capabilities","home-assistant","Manage","mdi:tools")}
      ${card(panel,"Memory & Knowledge",`${checks.memory?.value || "Memory status unavailable"} · Knowledge ${checks.knowledge?.value || "status unavailable"}`,loading.memory || loading.knowledge ? "Stored-data counts are loading." : `${result.load_errors?.some(issue => issue.key === "memory") ? "Memory count unavailable" : `${memoryCount} ${agent.memory_count === 1 ? "memory" : "memories"}`} · ${result.load_errors?.some(issue => issue.key === "knowledge") ? "Knowledge count unavailable" : `${knowledgeCount} Knowledge sources`}`,"data-memory","memories","Manage","mdi:brain")}
      ${card(panel,"Conversation history",agent.archive_enabled ? "Archive enabled" : "Archive disabled",result.load_errors?.some((issue) => issue.key === "conversations") ? "Retention unavailable" : `Retention: ${conversations.archive_retention_days || 30} days`,"data-memory","conversations","View","mdi:message-text-clock-outline",archiveTone)}
      ${card(panel,"Guest Mode",loading.guest_mode ? "Loading…" : result.load_errors?.some(issue => issue.key === "guest_mode") ? "Status unavailable" : panel._titleCase(String(guest.state || "inactive").replaceAll("_"," ")),loading.guest_mode ? "Loading current Guest Mode status." : "Integration-enforced visitor access and data restrictions.","capabilities","guest-mode","Configure","mdi:account-lock-outline",guestTone)}
      ${card(panel,"Usage",loading.usage ? "Loading usage…" : result.load_errors?.some((issue) => issue.key === "usage") ? "Usage unavailable" : `${Number(usage.today?.total_tokens || 0).toLocaleString()} tokens today`,loading.usage ? "Loading today and monthly totals." : result.load_errors?.some((issue) => issue.key === "usage") ? "Usage summary could not be loaded." : `${Number(usage.month?.total_tokens || 0).toLocaleString()} this month`,"usage-maintenance","usage","View","mdi:chart-line")}
      ${card(panel,"Web Search",web?.value || "Status unavailable",web?.detail || "Web Search status could not be loaded.",web?.action?.page || "capabilities",web?.action?.subsection || "web-skills","Configure","mdi:web",web?.state === "warning" ? "warning" : web?.state === "ready" ? "positive" : "neutral")}
    </section>`)}
    ${warnings.length ? `<section class="overview-warnings" aria-label="Actionable warnings">${warnings.map((warning) => `<div class="notice warning"><strong>Review recommended</strong><p>${panel._e(warning)}</p></div>`).join("")}</section>` : ""}

    <section id="broadcast-card" class="content-card" aria-label="Broadcast"><p class="empty">Loading Broadcast…</p></section>`;
}

export function bindOverview(panel, broadcastPromise) {
  const details = panel.shadowRoot.querySelector("#overview-health-details");
  if (details) {
    details.open = panel._overviewHealthExpanded === panel._agentId;
    details.addEventListener("toggle", () => { if (details.isConnected) panel._overviewHealthExpanded = details.open ? panel._agentId : null; });
  }
  panel.shadowRoot.querySelectorAll(".dashboard-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
  panel.shadowRoot.querySelectorAll(".setup-health-action").forEach((button) => button.addEventListener("click", async () => {
    panel._pendingSettingFocus = button.dataset.target || "";
    await panel._navigate(button.dataset.page, button.dataset.subsection || null);
  }));
  if (!broadcastPromise) return;
  void import("./overview-broadcast.js")
    .then((module) => module.bindBroadcast(panel, broadcastPromise))
    .catch((err) => {
      if (panel._viewKey?.() !== "overview") return;
      const host = panel.shadowRoot.querySelector("#broadcast-card");
      if (host) host.innerHTML = `<div class="error" role="alert">Unable to load Broadcast: ${panel._e(err.message || String(err))}</div>`;
    });
}
