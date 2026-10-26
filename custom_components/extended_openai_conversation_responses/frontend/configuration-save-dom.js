import {same, saveBarMarkup} from "./unsaved-state.js";
import {getRouteFeature} from "./management-route.js";

// Save controls can disappear while an in-flight write's draft becomes clean.
// Their lifetime is independent of the form structure reconciled below.
function reconcileSaveBar(panel, main) {
  let bar = main.querySelector(".save-bar");
  if (!panel._configDirty) { bar?.remove(); return true; }
  const pending = Boolean(panel._configurationSaving);
  if (!bar) {
    const anchor = main.querySelector("#save-bar-anchor") || main.querySelector("#memory-settings-save-anchor");
    if (!anchor) {
      // A missing insertion point makes this live structure uncertifiable.
      // Let the caller render it again instead of recording markup not in DOM.
      panel._eocMainMarkup = null;
      return false;
    }
    anchor.insertAdjacentHTML("beforebegin", saveBarMarkup({configuration:true, pending}));
    bar = main.querySelector(".save-bar");
  }
  const save = bar.querySelector("#save-config"), discard = bar.querySelector("#revert-config");
  if (save) {
    save.disabled = pending;
    save.textContent = pending ? "Saving…" : "Save changes";
  }
  if (discard) discard.disabled = pending;
  return true;
}

// Reconcile only an unchanged form structure. Plan form edits before applying them.
const mutableAttributes = new Set(["value", "checked", "selected", "disabled", "hidden", "aria-invalid", "data-initial-value"]);

export function reconcileSavedConfiguration(panel, owner) {
  const main = panel.shadowRoot?.querySelector?.("main");
  if (!main || main !== owner.main || panel._agentId !== owner.agentId
      || panel._viewKey?.() !== owner.view || panel._eocRenderedRoute !== owner.route
      || !owner.markup || panel._draft?.chat_model !== owner.model
      || !same(panel._draft?.voice_device_mappings || {}, owner.voiceMappings)
      || panel.shadowRoot.querySelector("dialog[open]")) return false;
  if (!reconcileSaveBar(panel, main)) return false;
  const markup = panel._content(panel._selectedAgent());
  if (owner.view === "assistant/voice") {
    const draft = panel._draft;
    const defaultActive = draft.voice_scope_policy === "default_user" || (draft.voice_scope_policy === "device_mapping" && draft.voice_unmapped_policy === "default_user");
    const feature = getRouteFeature(owner.view);
    if (!feature?.updatePolicy || draft.voice_scope_policy !== owner.voicePolicy || defaultActive !== owner.voiceDefaultActive) return false;
    // Voice moves its own cards and hydrates assignments outside the renderer.
    for (const control of main.querySelectorAll("[data-config]")) {
      const value = draft[control.dataset.config];
      if (value !== undefined && control.value !== String(value ?? "")) control.value = String(value ?? "");
    }
    const picker = main.querySelector("#config-voice_default_user_picker");
    const user = String(draft.voice_default_user_id || "").replace(/^user:/, "");
    if (picker && picker.value !== user) picker.value = user;
    feature.updatePolicy(panel);
    panel._eocMainMarkup = markup;
    return true;
  }
  const oldTemplate = document.createElement("template"), nextTemplate = document.createElement("template");
  oldTemplate.innerHTML = owner.markup; nextTemplate.innerHTML = markup;
  for (const template of [oldTemplate, nextTemplate]) template.content.querySelector(".save-bar")?.remove();
  const edits = [];
  const children = node => [...node.childNodes].filter(child => !child.matches?.(".save-bar"));
  const plan = (old, next, live) => {
    if (!live || old.nodeType !== next.nodeType || (old.nodeType !== Node.DOCUMENT_FRAGMENT_NODE && old.nodeType !== live.nodeType)) return false;
    if (old.nodeType === Node.TEXT_NODE) {
      if (old.textContent !== next.textContent) edits.push(() => { live.textContent = next.textContent; });
      return true;
    }
    if (old.nodeType === Node.ELEMENT_NODE) {
      if (old.tagName !== next.tagName || old.tagName !== live.tagName) return false;
      for (const name of ["id", "data-config", "type", "name"]) {
        if (old.hasAttribute(name) && old.getAttribute(name) !== live.getAttribute(name)) return false;
      }
      for (const name of new Set([...old.getAttributeNames(), ...next.getAttributeNames()])) {
        if (old.getAttribute(name) === next.getAttribute(name)) continue;
        if (!mutableAttributes.has(name)) return false;
        edits.push(() => {
          if (next.hasAttribute(name)) live.setAttribute(name, next.getAttribute(name));
          else live.removeAttribute(name);
          if (name === "data-initial-value" && live.value !== next.getAttribute(name)) live.value = next.getAttribute(name);
        });
      }
      if (["INPUT", "SELECT", "TEXTAREA"].includes(next.tagName)) {
        edits.push(() => {
          if (live.value !== next.value) live.value = next.value;
          if (next.tagName === "INPUT" && live.checked !== next.checked) live.checked = next.checked;
        });
      }
      // Native widgets and lazy feature containers own their hydrated children.
      if (next.tagName.includes("-") || next.hasAttribute("data-voice-mapping-feature")) return true;
    }
    const a = children(old), b = children(next), c = children(live);
    if (a.length !== b.length || a.length !== c.length) return false;
    for (let i = 0; i < a.length; i++) if (!plan(a[i], b[i], c[i])) return false;
    return true;
  };
  if (!plan(oldTemplate.content, nextTemplate.content, main)) return false;
  for (const edit of edits) edit();
  panel._eocMainMarkup = markup;
  return true;
}
