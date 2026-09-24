
let implementation = null;
let loadPromise = null;
let healthClarityModule = null;
let healthClarityPromise = null;
let onboardingModule = null;
let onboardingPromise = null;

const WS_MANAGEMENT = "extended_openai_conversation_responses/management";
const WS_BROADCAST = "extended_openai_conversation_responses/broadcast";

export async function ensureOverviewModule() {
  if (implementation) return implementation;
  if (!loadPromise) {
    loadPromise = import("./overview-page-impl.js")
      .then((module) => {
        implementation = module;
        return module;
      })
      .finally(() => { loadPromise = null; });
  }
  return loadPromise;
}

export function startOverviewBroadcastSnapshot(panel) {
  if (panel._eocOverviewBroadcastPromise) return panel._eocOverviewBroadcastPromise;
  const promise = panel._hass.callWS({type: WS_BROADCAST, action: "snapshot"});
  panel._eocOverviewBroadcastPromise = promise;
  return promise;
}

if (typeof document === "undefined") await ensureOverviewModule();

function queueRender(panel) {
  void ensureOverviewModule()
    .then(() => panel._render?.())
    .catch((err) => {
      panel._error = `Unable to load Overview: ${err.message || String(err)}`;
      panel._render?.();
    });
}

function overviewAgentFeatureProjection(agent) {
  const memory = agent?.feature_status?.memory;
  const knowledge = agent?.feature_status?.knowledge;
  if (!memory && !knowledge) return agent;
  const memoryLabel = memory?.label || agent.memory_mode || "Unknown";
  const labels = [memoryLabel];
  if (knowledge?.label) labels.push(`Knowledge ${knowledge.label}`);
  return {...agent, memory_mode: labels.join(" · ")};
}

function snapshotCard(panel, title, status, detail, pageName, section, action) {
  return `<article class="dashboard-card eoc-overview-snapshot-card"><div><h2>${panel._e(title)}</h2><strong>${panel._e(String(status))}</strong><p>${panel._e(detail)}</p></div><button type="button" class="secondary dashboard-action" data-page="${pageName}" data-subsection="${section}">${action}</button></article>`;
}

export function renderOverviewSnapshot(panel, sourceAgent) {
  const agent = overviewAgentFeatureProjection(sourceAgent);
  const guest = agent?.guest_mode || {};
  const memoryMode = panel._titleCase?.(agent?.memory_mode || "Unknown") || String(agent?.memory_mode || "Unknown");
  const guestState = panel._titleCase?.(String(guest.state || "inactive").replaceAll("_", " ")) || String(guest.state || "inactive");
  return `<section class="page-intro eoc-overview-snapshot-intro"><h1>${panel._e(agent?.title || "Assistant")}</h1><p>Your assistant at a glance. Detailed health, usage, and stored-data counts are still loading.</p></section>
    <section class="dashboard-grid eoc-overview-snapshot" aria-label="Assistant overview" aria-busy="true">
      ${snapshotCard(panel, "Assistant", `${agent?.provider || "Unknown"} · ${agent?.model || "Unknown"}`, "Model, responses, conversation behavior, prompt, and voice.", "assistant", "basics", "Configure")}
      ${snapshotCard(panel, "Capabilities", `${agent?.function_count == null ? "Loading…" : `${Number(agent.function_count).toLocaleString()} functions`} · ${Number(agent?.function_group_count || 0).toLocaleString()} groups`, "Home Assistant access, custom functions, and visitor restrictions.", "capabilities", "home-assistant", "Manage")}
      ${snapshotCard(panel, "Memory & Knowledge", memoryMode, "Stored memory and Knowledge counts are loading.", "data-memory", "memories", "Manage")}
      ${snapshotCard(panel, "Conversation history", agent?.archive_enabled ? "Archive enabled" : "Archive disabled", "Retention details are loading.", "data-memory", "conversations", "View")}
      ${snapshotCard(panel, "Guest Mode", guestState, "Integration-enforced visitor access and data restrictions.", "capabilities", "guest-mode", "Configure")}
      ${snapshotCard(panel, "Usage", "Loading usage…", "Today's and monthly token totals are loading.", "usage-maintenance", "usage", "View")}
    </section>`;
}

function bindSnapshotOverview(panel) {
  panel.shadowRoot?.querySelectorAll?.(".eoc-overview-snapshot .dashboard-action").forEach((button) => {
    button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection));
  });
}

function ensureOnboardingModule() {
  if (onboardingModule) return Promise.resolve(onboardingModule);
  if (!onboardingPromise) {
    onboardingPromise = import("./overview-onboarding.js")
      .then((module) => {
        onboardingModule = module;
        return module;
      })
      .finally(() => { onboardingPromise = null; });
  }
  return onboardingPromise;
}

function ensureOverviewHealthClarityModule() {
  if (healthClarityModule) return Promise.resolve(healthClarityModule);
  if (!healthClarityPromise) {
    healthClarityPromise = import("./management-overview-health-clarity.js")
      .then((module) => {
        healthClarityModule = module;
        return module;
      })
      .finally(() => { healthClarityPromise = null; });
  }
  return healthClarityPromise;
}

export function renderOverview(panel, agent) {
  if (implementation) {
    return implementation.renderOverview(panel, overviewAgentFeatureProjection(agent));
  }
  queueRender(panel);
  return renderOverviewSnapshot(panel, agent);
}

export function bindOverview(panel) {
  if (!implementation) {
    bindSnapshotOverview(panel);
    return;
  }
  // The agent snapshot and summary are the first useful Overview. Broadcast
  // starts after the summary is mounted so its request cannot contend for it.
  const detailsPending = Object.values(panel._result?.loading || {}).some(Boolean);
  const broadcast = panel._result && !detailsPending
    ? panel._eocOverviewBroadcastPromise || startOverviewBroadcastSnapshot(panel)
    : null;
  implementation.bindOverview(panel, broadcast);
  void ensureOnboardingModule()
    .then((module) => {
      if (panel?._viewKey?.() === "overview") module.bindGettingStarted(panel);
    })
    .catch(() => {});
}

export function enhanceOverviewHealthClarity(panel) {
  if (!implementation || panel?._viewKey?.() !== "overview") return;
  void ensureOverviewHealthClarityModule()
    .then((module) => {
      if (panel?._viewKey?.() === "overview") module.enhanceOverviewHealthClarity(panel);
    })
    .catch(() => {});
}


const OVERVIEW_DETAIL_LABELS = {
  usage: "Usage",
  memory: "Memory",
  knowledge: "Knowledge",
  guest_mode: "Guest Mode",
  setup_health: "Setup health",
};

function mergeSetupHealthFacts(current = {}, patch = {}) {
  const next = {...current};
  for (const [key, value] of Object.entries(patch || {})) {
    next[key] = value && typeof value === "object" && !Array.isArray(value)
      ? {...(current?.[key] || {}), ...value}
      : value;
  }
  return next;
}

export function startOverviewDetailReads(panel, {loadToken, cacheGeneration} = {}) {
  const agent = panel._selectedAgent?.();
  if (!agent || panel._viewKey?.() !== "overview") return null;
  const agentId = agent.subentry_id;
  const entryId = agent.entry_id;
  const token = loadToken ?? panel._loadToken;
  const generation = cacheGeneration ?? panel._cacheGeneration;
  const state = {
    agentId,
    entryId,
    token,
    generation,
    result: panel._result || {},
    remaining: new Set(Object.keys(OVERVIEW_DETAIL_LABELS)),
  };
  panel._eocOverviewDetailState = state;

  const patch = (kind, settled) => {
    if (panel._eocOverviewDetailState !== state
        || panel._agentId !== agentId
        || panel._cacheGeneration !== generation) return;
    const loading = {...(state.result.loading || {}), [kind]: false};
    const errors = (state.result.load_errors || []).filter((issue) => issue.key !== kind);
    let next = {...state.result, loading, load_errors: errors};

    if (settled.status === "fulfilled") {
      const value = settled.value || {};
      if (value.usage) next.usage = value.usage;
      if (value.setup_health) {
        next.setup_health = mergeSetupHealthFacts(next.setup_health, value.setup_health);
      }
      if (value.agent) Object.assign(panel._selectedAgent?.() || {}, value.agent);
    } else {
      next.load_errors = [...errors, {
        key: kind,
        label: OVERVIEW_DETAIL_LABELS[kind],
        message: settled.reason?.message || String(settled.reason || "Unknown error"),
      }];
      if (kind === "memory" || kind === "knowledge") {
        next.setup_health = mergeSetupHealthFacts(next.setup_health, {
          [kind]: {available: false, loading: false},
        });
      }
    }

    state.result = next;
    state.remaining.delete(kind);
    if (panel._viewKey?.() === "overview" && panel._loadToken === token) {
      panel._result = next;
      panel._render?.();
    }
    if (!state.remaining.size) {
      const key = panel._sectionCacheKey?.("overview");
      if (key) {
        panel._sectionCache?.set(key, next);
        panel._eocSectionCacheTimes?.set(key, Date.now());
      }
    }
  };

  const reads = Object.keys(OVERVIEW_DETAIL_LABELS).map((kind) =>
    panel._hass.callWS({
      type: WS_MANAGEMENT,
      section: "overview",
      action: "detail",
      kind,
      entry_id: entryId,
      subentry_id: agentId,
    }).then(
      (value) => patch(kind, {status: "fulfilled", value}),
      (reason) => patch(kind, {status: "rejected", reason}),
    ),
  );
  return Promise.allSettled(reads);
}
