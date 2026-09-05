import {bindGettingStarted} from "./overview-onboarding.js";

let implementation = null;
let loadPromise = null;

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

export function renderOverview(panel, agent) {
  if (implementation) {
    return implementation.renderOverview(panel, overviewAgentFeatureProjection(agent));
  }
  queueRender(panel);
  return panel._loading?.() || '<div class="loading">Loading Overview…</div>';
}

export function bindOverview(panel) {
  implementation?.bindOverview?.(panel);
  bindGettingStarted(panel);
}
