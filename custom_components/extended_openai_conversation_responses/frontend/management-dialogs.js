// Core dialogs have host lifetime. Handlers read current panel state at dispatch,
// so refreshing a route neither discards an editor nor adds duplicate listeners.
export const PERSISTENT_DIALOGS = new Set([
  "knowledge-dialog", "memory-dialog", "session-dialog", "reassign-dialog", "confirm-dialog",
]);

export function bindPanelDialogs(panel) {
  const root = panel.shadowRoot;
  if (root.__eocDialogsBound) return;
  root.__eocDialogsBound = true;
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    const dialog = button?.closest?.("dialog");
    if (!PERSISTENT_DIALOGS.has(dialog?.id)) return;
    const actions = {
      "confirm-cancel": () => panel._resolveConfirm(false),
      "confirm-accept": () => panel._resolveConfirm(true),
      "knowledge-delete": () => panel._deleteSource(panel._editingSource?.source_id, true),
      "memory-delete": () => panel._deleteMemory(panel._editingMemory?.memory_id, true),
      "reassign-cancel": () => dialog.close(),
      "reassign-save": () => panel._saveReassign(),
    };
    if (actions[button.id]) actions[button.id]();
    else if (button.classList.contains("close-editor")) panel._requestEditorClose();
    else if (button.classList.contains("close-session")) dialog.close();
  });
  root.addEventListener("submit", (event) => {
    if (!["knowledge-form", "memory-form"].includes(event.target?.id)) return;
    event.preventDefault();
    if (event.target.id === "knowledge-form") void panel._saveKnowledge();
    else void panel._saveMemory();
  });
  root.addEventListener("input", (event) => {
    if (event.target?.id === "knowledge-content") panel._updateKnowledgeCounter();
  });
  root.addEventListener("cancel", (event) => {
    const dialog = event.target;
    if (!PERSISTENT_DIALOGS.has(dialog?.id)) return;
    event.preventDefault();
    if (dialog.id === "confirm-dialog") panel._resolveConfirm(false);
    else if (["knowledge-dialog", "memory-dialog"].includes(dialog.id)) panel._requestEditorClose();
    else dialog.close();
  }, true);
}

export function updateDialogs(panel, markup) {
  const root = panel.shadowRoot;
  const host = root.querySelector("#eoc-dialog-host");
  if (!host) return;
  const template = document.createElement("template");
  template.innerHTML = markup;
  // Feature editors still have per-render bindings. Replace those until their
  // owners migrate; never replace or detach a core dialog, particularly an open one.
  for (const child of [...host.children]) {
    if (!PERSISTENT_DIALOGS.has(child.id)) child.remove();
  }
  for (const child of [...template.content.children]) {
    if (!PERSISTENT_DIALOGS.has(child.id) || !host.querySelector(`#${child.id}`)) host.append(child);
    else if (child.id === "reassign-dialog" && !host.querySelector("#reassign-dialog").open) {
      host.querySelector("#reassign-scope").innerHTML = child.querySelector("#reassign-scope").innerHTML;
    }
  }
}
