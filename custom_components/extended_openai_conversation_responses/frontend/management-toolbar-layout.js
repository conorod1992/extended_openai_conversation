const PATCHED = Symbol.for("extended-openai.management-toolbar-layout");

const TOOLBAR_STYLE = `
  .eoc-management-toolbar{
    display:grid;
    grid-template-columns:minmax(220px,360px) minmax(280px,1fr);
    gap:12px;
    align-items:end;
    margin:0 0 12px;
    padding:10px 12px;
    border:1px solid var(--divider-color);
    border-radius:12px;
    background:var(--card-background-color);
  }
  .eoc-management-toolbar .agent-picker{
    min-width:0;
    margin:0;
  }
  .eoc-management-toolbar .global-search.eoc-global-search{
    min-width:0;
    margin:0;
  }
  .eoc-management-toolbar .eoc-global-search>label{
    display:block;
  }
  .eoc-management-toolbar .eoc-global-search .search-label{
    display:none;
  }
  .eoc-management-toolbar .eoc-global-search .search-results{
    width:min(760px,100%);
  }
  .eoc-management-toolbar+.top-nav{
    margin-top:0;
  }
  .top-nav+.subsection-nav{
    margin-top:0;
  }
  @media (max-width:800px){
    .eoc-management-toolbar{
      grid-template-columns:1fr;
      align-items:stretch;
    }
  }
`;

function unwrapExistingToolbar(root) {
  const toolbar = root.querySelector(".eoc-management-toolbar");
  if (!toolbar) return;
  while (toolbar.firstChild) toolbar.before(toolbar.firstChild);
  toolbar.remove();
}

export function applyManagementToolbarLayout(panel) {
  const root = panel?.shadowRoot;
  if (!root) return false;

  root.querySelector("style[data-eoc-management-toolbar]")?.remove();
  const style = document.createElement("style");
  style.dataset.eocManagementToolbar = "";
  style.textContent = TOOLBAR_STYLE;
  root.append(style);

  unwrapExistingToolbar(root);

  const topNav = root.querySelector(".top-nav");
  const agentPicker = root.querySelector(".agent-picker");
  const settingsSearch = root.querySelector(".eoc-global-search");
  if (!topNav || (!agentPicker && !settingsSearch)) return false;

  const toolbar = document.createElement("div");
  toolbar.className = "eoc-management-toolbar";
  toolbar.setAttribute("role", "group");
  toolbar.setAttribute("aria-label", "Assistant and settings controls");
  if (agentPicker) toolbar.append(agentPicker);
  if (settingsSearch) toolbar.append(settingsSearch);
  topNav.before(toolbar);

  const subsectionNav = root.querySelector(".subsection-nav");
  if (subsectionNav) topNav.after(subsectionNav);
  return true;
}

export function installManagementToolbarLayout(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      applyManagementToolbarLayout(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementToolbarLayout();
}

export {TOOLBAR_STYLE};
