const PANEL_TAG = "extended-openai-management-panel";
const PATCHED = Symbol.for("extended-openai.management-initial-bootstrap");

function showInitialLoading(panel) {
  if (panel?._data !== null) return false;
  const main = panel.shadowRoot?.querySelector?.("main");
  if (!main) return false;
  main.innerHTML = panel._loading?.() || '<div class="loading" role="status">Loading…</div>';
  main.setAttribute("aria-busy", "true");
  main.dataset.eocInitialLoading = "";
  return true;
}

function installManagementInitialBootstrap(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined(PANEL_TAG).then(() => {
    const constructor = registry.get(PANEL_TAG);
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      showInitialLoading(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementInitialBootstrap();
}

export {installManagementInitialBootstrap, showInitialLoading};
