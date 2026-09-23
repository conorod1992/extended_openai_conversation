import {buildSetupHealth} from "./overview-health.js";

function card(panel, title, status, detail, page, section, action, icon, tone = "neutral") {
  return `<article class="dashboard-card dashboard-tone-${tone}"><div class="dashboard-card-main"><span class="dashboard-icon" aria-hidden="true"><ha-icon icon="${panel._e(icon)}"></ha-icon></span><div><h2>${panel._e(title)}</h2><strong>${panel._e(String(status))}</strong><p>${panel._e(detail)}</p></div></div><button type="button" class="secondary dashboard-action" data-page="${page}" data-subsection="${section}">${action}</button></article>`;
}

const HEALTH_ICONS = {
  ready: "mdi:check-circle-outline",
  warning: "mdi:alert-circle-outline",
  error: "mdi:close-circle-outline",
  neutral: "mdi:minus-circle-outline",
  unknown: "mdi:help-circle-outline",
};

function healthActionLabel(check) {
  return {
    provider_runtime: "Run live test",
    instructions: "Review",
    home_assistant_exposure: "Review",
    memory: "Configure",
    knowledge: "Manage",
    web_search: check.state === "warning" ? "Fix" : "Configure",
  }[check.id] || "Review";
}

function setupHealthMarkup(panel, health) {
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
  return `<section class="setup-health setup-health-${state}" aria-label="Setup and health">
    <div class="setup-health-heading">
      <div><span class="section-kicker"><ha-icon icon="mdi:heart-pulse"></ha-icon> Configuration</span><h2>Setup & health</h2><p>Quick checks for the selected assistant. Optional features that are off by choice are not treated as problems.</p></div>
      <div class="setup-health-summary" role="status"><strong>${panel._e(summaryTitle)}</strong><span>${panel._e(summaryDetail)}</span></div>
    </div>
    <div class="setup-health-grid">
      ${health.checks.map((check) => {
        const checkState = HEALTH_ICONS[check.state] ? check.state : "unknown";
        const actionMarkup = health.can_manage && check.action
          ? `<button type="button" class="secondary setup-health-action" data-page="${panel._e(check.action.page || "")}" data-subsection="${panel._e(check.action.subsection || "")}" data-target="${panel._e(check.action.target || "")}">${panel._e(healthActionLabel(check))}</button>`
          : "";
        return `<article class="setup-health-check setup-health-check-${checkState}"><ha-icon class="setup-health-icon" icon="${HEALTH_ICONS[checkState]}" aria-hidden="true"></ha-icon><div class="setup-health-copy"><span>${panel._e(check.title)}</span><strong>${panel._e(check.value)}</strong><p>${panel._e(check.detail)}</p></div>${actionMarkup}</article>`;
      }).join("")}
    </div>
    <p class="setup-health-footnote"><ha-icon icon="mdi:information-outline" aria-hidden="true"></ha-icon> Connection tests only run when you start one from Diagnostics.</p>
  </section>`;
}

export function renderOverview(panel, agent) {
  const result = panel._result || {};
  const usage = result.usage || {};
  const conversations = result.conversations || {};
  const setupHealth = buildSetupHealth(result.setup_health || {});
  const guest = agent.guest_mode || {};
  const warnings = [];
  if (["active", "active_indefinitely"].includes(guest.state) && !guest.has_home_assistant_exclusions) warnings.push("Guest Mode is active without configured Home Assistant exclusions.");
  for (const issue of result.load_errors || []) warnings.push(`${issue.label} could not be loaded. Other overview information is still available.`);
  const loading = result.loading || {};
  const memoryCount = Number(agent.memory_count || 0).toLocaleString();
  const knowledgeCount = Number(agent.knowledge_source_count || 0).toLocaleString();
  const archiveTone = agent.archive_enabled ? "positive" : "neutral";
  const guestTone = ["active", "active_indefinitely", "scheduled"].includes(guest.state) ? "warning" : "neutral";
  return `<style>
      .dashboard-card-main{display:flex;align-items:flex-start;gap:15px;min-width:0}
      .dashboard-icon{display:grid;place-items:center;flex:0 0 42px;width:42px;height:42px;border-radius:11px;background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color));color:var(--primary-color)}
      .dashboard-icon ha-icon{--mdc-icon-size:23px}
      .dashboard-card{background:color-mix(in srgb,var(--secondary-background-color) 34%,var(--card-background-color));border-color:color-mix(in srgb,var(--divider-color) 72%,var(--secondary-text-color));box-shadow:0 1px 2px rgba(0,0,0,.04)}
      .dashboard-card:hover{border-color:color-mix(in srgb,var(--primary-color) 40%,var(--divider-color));box-shadow:0 3px 10px rgba(0,0,0,.07)}
      .dashboard-tone-positive .dashboard-icon{color:var(--success-color,#0f9d58);background:color-mix(in srgb,var(--success-color,#0f9d58) 10%,var(--card-background-color))}
      .dashboard-tone-warning .dashboard-icon{color:var(--warning-color,#b26a00);background:color-mix(in srgb,var(--warning-color,#f9ab00) 12%,var(--card-background-color))}
      .dashboard-card h2{font-size:18px}.dashboard-card strong{font-size:19px;line-height:1.35}.dashboard-card p{font-size:14px}
      .section-kicker{display:flex;align-items:center;gap:7px;margin-bottom:7px;color:var(--primary-color);font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:.045em}
      .section-kicker ha-icon{--mdc-icon-size:18px}
      .setup-health{margin:0 0 26px;padding:22px;border:1px solid var(--divider-color);border-radius:14px;background:color-mix(in srgb,var(--secondary-background-color) 22%,var(--card-background-color))}
      .setup-health-ready{border-left:4px solid var(--success-color,#0f9d58)}.setup-health-warning{border-left:4px solid var(--warning-color,#b26a00)}.setup-health-error{border-left:4px solid var(--error-color,#db4437)}
      .setup-health-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:22px;margin-bottom:16px}.setup-health-heading h2{margin:0;font-size:21px}.setup-health-heading p{margin:5px 0 0;color:var(--secondary-text-color);line-height:1.45}
      .setup-health-summary{display:grid;gap:2px;flex:0 0 auto;min-width:150px;padding:10px 13px;border-radius:10px;background:var(--card-background-color);border:1px solid var(--divider-color)}.setup-health-summary strong{font-size:15px}.setup-health-summary span{font-size:12px;color:var(--secondary-text-color)}
      .setup-health-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.setup-health-check{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:11px;align-items:start;padding:13px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color)}
      .setup-health-icon{margin-top:2px;--mdc-icon-size:21px;color:var(--secondary-text-color)}.setup-health-check-ready .setup-health-icon{color:var(--success-color,#0f9d58)}.setup-health-check-warning .setup-health-icon{color:var(--warning-color,#b26a00)}.setup-health-check-error .setup-health-icon{color:var(--error-color,#db4437)}
      .setup-health-copy{display:grid;gap:3px;min-width:0}.setup-health-copy>span{font-size:12px;color:var(--secondary-text-color)}.setup-health-copy>strong{font-size:15px;line-height:1.35}.setup-health-copy p{margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.4}.setup-health-action{align-self:center;min-height:34px;padding:5px 10px;white-space:nowrap}
      .setup-health-footnote{display:flex;align-items:flex-start;gap:7px;margin:13px 0 0;color:var(--secondary-text-color);font-size:12px;line-height:1.45}.setup-health-footnote ha-icon{flex:0 0 auto;--mdc-icon-size:17px}
      @media (max-width:800px){.setup-health-grid{grid-template-columns:1fr}.setup-health-heading{display:grid}.setup-health-summary{min-width:0}.setup-health-check{grid-template-columns:auto minmax(0,1fr)}.setup-health-action{grid-column:2;justify-self:start}}
    </style>
    <section class="page-intro"><h1>${panel._e(agent.title)}</h1><p>Your assistant at a glance. Open a card to change or inspect that area.</p></section>
    ${setupHealthMarkup(panel, setupHealth)}
    ${warnings.length ? `<section class="overview-warnings" aria-label="Actionable warnings">${warnings.map((warning) => `<div class="notice"><strong>Review recommended</strong><p>${panel._e(warning)}</p></div>`).join("")}</section>` : ""}
    <section class="dashboard-grid" aria-label="Assistant overview">
      ${card(panel,"Assistant",`${agent.provider} · ${agent.model}`,"Model, responses, conversation behavior, prompt, and voice.","assistant","basics","Configure","mdi:robot-outline")}
      ${card(panel,"Capabilities",`${Number(agent.function_count || 0).toLocaleString()} functions · ${Number(agent.function_group_count || 0).toLocaleString()} groups`,"Home Assistant access, custom functions, and visitor restrictions.","capabilities","home-assistant","Manage","mdi:tools")}
      ${card(panel,"Memory & Knowledge",panel._titleCase(agent.memory_mode),loading.memory || loading.knowledge ? "Stored-data counts are loading." : `${memoryCount} memories · ${knowledgeCount} Knowledge sources`,"data-memory","memories","Manage","mdi:brain")}
      ${card(panel,"Conversation history",agent.archive_enabled ? "Archive enabled" : "Archive disabled",result.load_errors?.some((issue) => issue.key === "conversations") ? "Retention unavailable" : `Retention: ${conversations.archive_retention_days || 30} days`,"data-memory","conversations","View","mdi:message-text-clock-outline",archiveTone)}
      ${card(panel,"Guest Mode",loading.guest_mode ? "Loading…" : panel._titleCase(String(guest.state || "inactive").replaceAll("_"," ")),loading.guest_mode ? "Loading current Guest Mode status." : "Integration-enforced visitor access and data restrictions.","capabilities","guest-mode","Configure","mdi:account-lock-outline",guestTone)}
      ${card(panel,"Usage",loading.usage ? "Loading usage…" : result.load_errors?.some((issue) => issue.key === "usage") ? "Usage unavailable" : `${Number(usage.today?.total_tokens || 0).toLocaleString()} tokens today`,loading.usage ? "Loading today and monthly totals." : result.load_errors?.some((issue) => issue.key === "usage") ? "Usage summary could not be loaded." : `${Number(usage.month?.total_tokens || 0).toLocaleString()} this month`,"usage-maintenance","usage","View","mdi:chart-line")}
    </section>
    <section id="broadcast-card" class="content-card" aria-label="Broadcast"><p class="empty">Loading Broadcast…</p></section>`;
}

export function bindOverview(panel, broadcastPromise) {
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
      if (host) host.innerHTML = `<div class="error" role="alert">Unable to load Broadcast: ${panel._e(err.message || String(err))}</div>`;
    });
}
