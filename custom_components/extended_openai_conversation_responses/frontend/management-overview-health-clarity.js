import {lookupModelData, modelDataStatusText, modelProviderForPanel} from "./model-catalog.js";
import {captureAuthentication} from "./authentication-presentation.js";
import {enhancementChanged} from "./management-enhancement-state.js";

function modelDataCardState(panel) {
  const data = panel?._modelCatalogData;
  const failed = ["download", "validation", "saved_requests", "persistence"].includes(data?.check_outcome);
  const error = panel?._eocOverviewModelDataError || data?.last_check_error || failed;
  if (error) {
    return {
      status: "Check failed",
      detail: data?.last_check_error ? modelDataStatusText(data) : "The current catalogue is still in use. Open Model Parameters to retry the check.",
      tone: "warning",
      action: "Review",
      mode: "navigate",
    };
  }
  if (!data) {
    return {
      status: "Loading…",
      detail: "Checking the current model capability data status.",
      tone: "neutral",
      action: "View",
      mode: "navigate",
    };
  }
  const current = Number(data.catalog_version);
  const available = Number(data.available_catalog_version);
  const currentLabel = Number.isInteger(current) ? `v${current}` : "current version";
  if (data.incompatible_catalog) {
    return {
      status: "Integration update required",
      detail: modelDataStatusText(data),
      tone: "warning",
      action: "Review",
      mode: "navigate",
    };
  }
  if (data.update_available) {
    const availableLabel = Number.isInteger(available) ? `v${available}` : "newer version";
    return {
      status: "Update available",
      detail: `${currentLabel} → ${availableLabel}. The newer catalogue is waiting for approval.`,
      tone: "warning",
      action: Number.isInteger(available) ? `Apply v${available}` : "Apply update",
      mode: "apply",
    };
  }
  if (data.last_error) {
    return {
      status: "Check failed",
      detail: data.last_error,
      tone: "warning",
      action: "Review",
      mode: "navigate",
    };
  }
  if (data.check_outcome === "unchecked" && data.source !== "bundled") {
    return {
      status: "Check pending",
      detail: modelDataStatusText(data),
      tone: "neutral",
      action: "View",
      mode: "navigate",
    };
  }
  if (data.source === "bundled") {
    return {
      status: "Using the built-in model catalogue",
      detail: `Catalogue ${currentLabel} · checked daily for newer data.`,
      tone: "neutral",
      action: "View",
      mode: "navigate",
    };
  }
  return {
    status: "Current",
    detail: `Catalogue ${currentLabel} · checked daily for newer data.`,
    tone: "positive",
    action: "View",
    mode: "navigate",
  };
}

export function renderOverviewModelDataCard(panel) {
  const grid = panel?.shadowRoot?.querySelector?.(".dashboard-grid");
  if (!grid || panel?._data?.is_admin === false) return false;
  grid.querySelector("[data-eoc-model-data-card]")?.remove();
  const state = modelDataCardState(panel);
  const article = document.createElement("article");
  article.className = `dashboard-card dashboard-tone-${state.tone}`;
  article.dataset.eocModelDataCard = "";
  article.innerHTML = `<div class="dashboard-card-main"><span class="dashboard-icon" aria-hidden="true"><ha-icon icon="mdi:database-sync-outline"></ha-icon></span><div><h3>Model Data</h3><strong>${panel._e(state.status)}</strong><p>${panel._e(state.detail)}</p></div></div><button type="button" class="secondary dashboard-action" data-eoc-model-data-action="${state.mode}">${panel._e(state.action)}</button>`;
  grid.append(article);

  article.querySelector('[data-eoc-model-data-action="navigate"]')?.addEventListener("click", () => {
    panel._navigate("assistant", "model-responses");
  });
  article.querySelector('[data-eoc-model-data-action="apply"]')?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const model = panel._selectedAgent?.()?.model || "";
      await lookupModelData(panel, model, "apply");
      panel._eocOverviewModelDataError = null;
      panel._toast?.("Model data update applied");
      if (panel._page === "overview") {
        renderOverviewModelDataCard(panel);
        await panel._loadSection?.(true);
      }
    } catch (err) {
      panel._toast?.(`Unable to apply model data update: ${err.message || String(err)}`, true);
      button.disabled = false;
    }
  });
  return true;
}

function ensureOverviewModelData(panel) {
  if (panel?._data?.is_admin === false || panel?._page !== "overview") return;
  const key = () => JSON.stringify([panel._loadToken, panel._agentId, panel._selectedAgent?.()?.model, modelProviderForPanel(panel)]);
  const visit = key();
  const authenticated = captureAuthentication(panel);
  if (panel._eocOverviewModelDataRead?.visit === visit
      && panel._eocOverviewModelDataRead.authenticated()) return;
  const read = {visit, authenticated};
  panel._eocOverviewModelDataRead = read;
  const current = () => authenticated() && panel._page === "overview"
    && panel._eocOverviewModelDataRead === read && key() === visit;
  const model = panel._selectedAgent?.()?.model || "";
  void lookupModelData(panel, model, "lookup", current)
    .then(() => { if (current()) panel._eocOverviewModelDataError = null; })
    .catch((err) => { if (current()) panel._eocOverviewModelDataError = err.message || String(err); })
    .finally(() => {
      if (current()) renderOverviewModelDataCard(panel);
    });
}

export function enhanceOverviewHealthClarity(panel) {
  if (panel._page !== "overview" || !panel.shadowRoot) return;
  ensureOverviewModelData(panel);
  if (!enhancementChanged(panel, "overview-health", [panel._agentId, panel._data?.is_admin, JSON.stringify(panel._modelCatalogData), panel._eocOverviewModelDataError])) return;
  renderOverviewModelDataCard(panel);
}
