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
    else if (button.classList.contains("close-editor")) {
      if (button.classList.contains("icon")) panel._requestEditorClose();
      else dialog.close();
    }
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

// Same-route dialog data refreshes retain editor nodes and their existing listeners.
// Compare against the last template, not live form values or imperative status text.
function patchDialog(current, previous, next) {
  if (previous.isEqualNode(next)) return;
  if (current.nodeType === Node.TEXT_NODE && next.nodeType === Node.TEXT_NODE) {
    current.textContent = next.textContent;
    return;
  }
  if (current.nodeName !== next.nodeName) { current.replaceWith(next.cloneNode(true)); return; }
  if (next.nodeType !== Node.ELEMENT_NODE) return;
  for (const attr of previous.attributes) {
    if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
  }
  for (const attr of next.attributes) {
    if (previous.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
  }
  const children = [...current.childNodes];
  const before = [...previous.childNodes];
  const after = [...next.childNodes];
  for (let index = 0; index < Math.max(before.length, after.length); index += 1) {
    if (!after[index]) children[index]?.remove();
    else if (!before[index] || !children[index]) current.append(after[index].cloneNode(true));
    else patchDialog(children[index], before[index], after[index]);
  }
}

export function updateDialogs(panel, markup, {preserveEditors = false} = {}) {
  const root = panel.shadowRoot;
  const host = root.querySelector("#eoc-dialog-host");
  if (!host) return;
  const template = document.createElement("template");
  template.innerHTML = markup;
  const previous = panel._eocDialogTemplate;
  panel._eocDialogTemplate = template.content.cloneNode(true);
  if (preserveEditors && previous) {
    for (const next of template.content.children) {
      const current = host.querySelector(`#${next.id}`);
      const before = previous.querySelector(`#${next.id}`);
      if (current && before && !current.open) patchDialog(current, before, next);
    }
    return;
  }
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
