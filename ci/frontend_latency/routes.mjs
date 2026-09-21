export const LATENCY_ROUTES = [
  {name: "overview", path: "overview"},
  {name: "assistant-basics", path: "assistant/basics"},
  {name: "assistant-model-responses", path: "assistant/model-responses"},
  {name: "assistant-conversation", path: "assistant/conversation"},
  {name: "assistant-prompt-context", path: "assistant/prompt-context"},
  {name: "assistant-voice", path: "assistant/voice"},
  {name: "assistant-speech", path: "assistant/speech"},
  {name: "capabilities-home-assistant", path: "capabilities/home-assistant"},
  {name: "capabilities-web-skills", path: "capabilities/web-skills"},
  {name: "capabilities-request-rules", path: "capabilities/request-rules"},
  {name: "capabilities-functions", path: "capabilities/functions"},
  {name: "capabilities-guest-mode", path: "capabilities/guest-mode"},
  {name: "capabilities-quiet-hours", path: "capabilities/quiet-hours"},
  {name: "data-memory-memories", path: "data-memory/memories"},
  {name: "data-memory-memory-settings", path: "data-memory/memory-settings"},
  {name: "data-memory-knowledge", path: "data-memory/knowledge"},
  {name: "conversation-history", path: "data-memory/conversations"},
  {name: "usage-maintenance-usage", path: "usage-maintenance/usage"},
  {name: "usage-maintenance-diagnostics", path: "usage-maintenance/diagnostics"},
  {name: "usage-maintenance-backup-restore", path: "usage-maintenance/backup-restore"},
  {name: "usage-maintenance-retention", path: "usage-maintenance/retention"},
  {name: "usage-maintenance-request-debug", path: "usage-maintenance/request-debug"},
];

export function expectedRouteState(route) {
  const [page, subsection = null] = route.path.split("/");
  return {page, subsection};
}

export async function managementRouteState(page) {
  const panel = page.locator("extended-openai-management-panel");
  if (await panel.count() === 0) return null;
  return panel.evaluate((element) => {
    if (!element?.shadowRoot) return null;
    return {
      page: element._page || null,
      subsection: element._subsection || null,
      busy: Boolean(element._busy),
      error: element._error || null,
      loading: Boolean(element.shadowRoot.querySelector("main .loading")),
      renderedRoute: element._eocRenderedRoute || null,
    };
  });
}

export async function waitForManagementRouteReady(page, route, timeout) {
  const panel = page.locator("extended-openai-management-panel");
  await panel.waitFor({state: "attached", timeout});

  const deadline = Date.now() + timeout;
  let lastState = null;
  while (Date.now() < deadline) {
    lastState = await panel.evaluate((element, path) => {
      if (!element?.shadowRoot) return null;
      const [pageName, subsection = null] = path.split("/");
      const state = {
        page: element._page || null,
        subsection: element._subsection || null,
        busy: Boolean(element._busy),
        error: element._error || null,
        loading: Boolean(element.shadowRoot.querySelector("main .loading")),
        renderedRoute: element._eocRenderedRoute || null,
      };
      state.ready = state.page === pageName
        && state.subsection === subsection
        && !state.busy
        && !state.error
        && !state.loading
        && String(state.renderedRoute || "").endsWith(`|${path}`);
      return state;
    }, route.path).catch(() => null);
    if (lastState?.ready) return;
    await page.waitForTimeout(50);
  }
  throw new Error(
    `Timed out waiting for management route ${route.path}; panel=${JSON.stringify(lastState)}`,
  );
}


export function routeStateMismatch(state, route) {
  if (!state) return false;
  const expected = expectedRouteState(route);
  return state.page !== expected.page || state.subsection !== expected.subsection;
}

export async function waitForLatencyRoute({
  route,
  baselineMode,
  waitReady,
  getState,
  probeTimeout = 2500,
  fullTimeout = 30000,
}) {
  if (!baselineMode) {
    await waitReady(fullTimeout);
    return {supported: true};
  }

  try {
    await waitReady(probeTimeout);
    return {supported: true};
  } catch (_probeError) {
    const probeState = await getState();
    if (routeStateMismatch(probeState, route)) {
      return {
        supported: false,
        unavailable_reason:
          `Historical route resolved to ${probeState.page || "unknown"}/${probeState.subsection || ""}`,
      };
    }
  }

  try {
    await waitReady(fullTimeout);
    return {supported: true};
  } catch (error) {
    const state = await getState();
    if (routeStateMismatch(state, route)) {
      return {
        supported: false,
        unavailable_reason:
          `Historical route resolved to ${state.page || "unknown"}/${state.subsection || ""}`,
      };
    }
    throw error;
  }
}
