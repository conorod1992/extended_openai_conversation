import {captureConfigurationOwner, configurationSubmissionIsCurrent} from "./configuration-acknowledgement.js";

export function renderConfigurationActions(panel) {
  const disabled = panel._configDirty;
  return `<details class="agent-actions-menu eoc-details-base"><summary aria-haspopup="menu">Assistant actions</summary><div role="menu"><button type="button" class="secondary" role="menuitem" id="duplicate-agent"${disabled ? " disabled" : ""}${panel._configDirty ? ' title="Save or revert the shared draft first"' : ""}>Duplicate agent</button><button type="button" class="secondary" role="menuitem" id="import-agent">Import configuration</button><p class="action-help"${panel._configDirty ? "" : " hidden"}>Duplicate agent uses the saved configuration. Save or revert your settings changes to enable it.</p></div></details>`;
}

// Bind the persistent shell once. The import editor stays off the page-load path.
export function bindAssistantActions(panel) {
  const root = panel.shadowRoot;
  const pending = new WeakSet();
  root.addEventListener("keydown", event => {
    const menu = event.target?.closest?.(".agent-actions-menu");
    if (event.key !== "Escape" || !menu?.open) return;
    menu.open = false;
    menu.querySelector("summary")?.focus();
  });
  root.addEventListener("click", async event => {
    const button = event.target?.closest?.(".agent-actions-menu :is(#duplicate-agent,#import-agent)");
    if (!button || button.disabled || pending.has(button) || panel._data?.is_admin !== true) return;
    const menu = button.closest("details");
    menu.open = false;
    menu.querySelector("summary")?.focus({preventScroll:true});
    const owner = captureConfigurationOwner(panel);
    const view = panel._viewKey();
    const current = () => panel.isConnected && !panel._eocPresentationBlocked && panel._data?.is_admin === true
      && button.isConnected && panel._viewKey() === view && configurationSubmissionIsCurrent(panel, owner);
    pending.add(button);
    try {
      const actions = await import("./assistant-configuration-actions.js");
      if (!current()) return;
      if (button.id === "duplicate-agent") await actions.duplicateSavedConfiguration(panel, button);
      else actions.openConfigurationImport(panel);
    } catch (error) {
      if (current()) panel._toast(`Unable to open Assistant actions: ${error.message || String(error)}`, true);
    } finally {
      pending.delete(button);
    }
  });
}
