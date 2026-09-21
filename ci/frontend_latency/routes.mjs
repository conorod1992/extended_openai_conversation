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

export async function waitForManagementRouteReady(page, route, timeout) {
  await page.waitForFunction(
    ({path}) => {
      const panel = document.querySelector("extended-openai-management-panel");
      if (!panel?.shadowRoot) return false;
      const [pageName, subsection = null] = path.split("/");
      if (panel._page !== pageName) return false;
      if ((panel._subsection || null) !== subsection) return false;
      if (panel._busy || panel._error) return false;
      const main = panel.shadowRoot.querySelector("main");
      if (!main || main.querySelector(".loading")) return false;
      const heading = main.querySelector(".page-intro h1");
      if (!heading?.textContent?.trim()) return false;
      return Boolean(
        main.querySelector(
          ".dashboard-grid,.config-section,.content-card,.list-card,.notice,extended-openai-debug-panel,.empty"
        )
      );
    },
    {path: route.path},
    {timeout},
  );
}
