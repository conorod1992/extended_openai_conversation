const PATCHED = Symbol.for("extended-openai.management-toolbar-layout");

const TOOLBAR_STYLE = `
  header .global-search.eoc-global-search{
    width:min(380px,100%);
    max-width:100%;
    min-width:280px;
    margin:0;
    align-self:end;
  }
  header .eoc-global-search>label{
    display:block;
  }
  header .eoc-global-search .search-label{
    display:none;
  }
  header .eoc-global-search .search-results{
    left:auto;
    right:0;
    width:min(760px,calc(100vw - 56px));
  }
  .eoc-agent-context-row{
    display:flex;
    align-items:end;
    margin:0 0 14px;
  }
  .eoc-agent-context-row .agent-picker{
    width:min(390px,100%);
    min-width:0;
    margin:0;
  }
  .eoc-agent-context-row .agent-picker.eoc-agent-context{
    min-width:0;
    padding:0;
    border:0;
    border-radius:0;
    background:transparent;
    box-shadow:none;
  }
  .eoc-agent-context-row+.top-nav{
    margin-top:0;
  }
  .top-nav+.subsection-nav{
    margin-top:0;
  }
  @media (max-width:800px){
    header{
      flex-direction:column;
      align-items:stretch;
      gap:18px;
    }
    header .global-search.eoc-global-search{
      width:100%;
      min-width:0;
      align-self:stretch;
    }
    header .eoc-global-search .search-results{
      width:100%;
      right:auto;
    }
    .eoc-agent-context-row{
      display:block;
      margin-bottom:18px;
    }
    .eoc-agent-context-row .agent-picker{
      width:100%;
    }
  }
`;

function clearPreviousLayout(root) {
  const toolbar = root.querySelector(".eoc-management-toolbar");
  if (toolbar) {
    while (toolbar.firstChild) toolbar.before(toolbar.firstChild);
    toolbar.remove();
  }

  const contextRow = root.querySelector(".eoc-agent-context-row");
  if (contextRow) {
    while (contextRow.firstChild) contextRow.before(contextRow.firstChild);
    contextRow.remove();
  }
}

export function applyManagementToolbarLayout(panel) {
  const root = panel?.shadowRoot;
  if (!root) return false;

  root.querySelector("style[data-eoc-management-toolbar]")?.remove();
  const style = document.createElement("style");
  style.dataset.eocManagementToolbar = "";
  style.textContent = TOOLBAR_STYLE;
  root.append(style);

  clearPreviousLayout(root);

  const header = root.querySelector("header");
  const topNav = root.querySelector(".top-nav");
  const agentPicker = root.querySelector(".agent-picker");
  const settingsSearch = root.querySelector(".eoc-global-search");
  if (!header || !topNav || (!agentPicker && !settingsSearch)) return false;

  if (settingsSearch) header.append(settingsSearch);

  if (agentPicker) {
    const contextRow = document.createElement("div");
    contextRow.className = "eoc-agent-context-row";
    contextRow.setAttribute("aria-label", "Assistant context");
    contextRow.append(agentPicker);
    topNav.before(contextRow);
  }

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
