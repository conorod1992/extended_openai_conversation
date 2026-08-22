function card(panel, title, status, detail, page, section, action) {
  return `<article class="dashboard-card"><div><h2>${panel._e(title)}</h2><strong>${panel._e(String(status))}</strong><p>${panel._e(detail)}</p></div><button type="button" class="secondary dashboard-action" data-page="${page}" data-subsection="${section}">${action}</button></article>`;
}

export function renderOverview(panel, agent) {
  const result = panel._result || {};
  const usage = result.usage || {};
  const conversations = result.conversations || {};
  const guest = agent.guest_mode || {};
  const warnings = [];
  if (["active", "active_indefinitely"].includes(guest.state) && !guest.has_home_assistant_exclusions) warnings.push("Guest Mode is active without configured Home Assistant exclusions.");
  return `<section class="page-intro"><h1>${panel._e(agent.title)}</h1><p>Your assistant at a glance. Open a card to change or inspect that area.</p></section>
    ${warnings.length ? `<section class="overview-warnings" aria-label="Actionable warnings">${warnings.map((warning) => `<div class="notice"><strong>Review recommended</strong><p>${panel._e(warning)}</p></div>`).join("")}</section>` : ""}
    <section class="dashboard-grid" aria-label="Assistant overview">
      ${card(panel,"Assistant",`${agent.provider} · ${agent.model}`,"Model, responses, conversation behavior, prompt, and voice.","assistant","basics","Configure")}
      ${card(panel,"Capabilities",`${agent.function_count || 0} functions · ${agent.function_group_count || 0} groups`,"Home Assistant access, custom functions, and visitor restrictions.","capabilities","home-assistant","Manage")}
      ${card(panel,"Memory & Knowledge",panel._titleCase(agent.memory_mode),`${agent.memory_count} memories · ${agent.knowledge_source_count} Knowledge sources`,"data-memory","memories","Manage")}
      ${card(panel,"Conversation history",agent.archive_enabled ? "Archive enabled" : "Archive disabled",`Retention: ${conversations.archive_retention_days || 30} days`,"data-memory","conversations","View")}
      ${card(panel,"Guest Mode",panel._titleCase(String(guest.state || "inactive").replaceAll("_"," ")),"Integration-enforced visitor access and data restrictions.","capabilities","guest-mode","Configure")}
      ${card(panel,"Usage",`${Number(usage.today?.total_tokens || 0).toLocaleString()} tokens today`,`${Number(usage.month?.total_tokens || 0).toLocaleString()} this month`,"usage-maintenance","usage","View")}
    </section>`;
}

export function bindOverview(panel) {
  panel.shadowRoot.querySelectorAll(".dashboard-action").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
}
