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
    gap:12px;
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
  .eoc-agent-actions{
    display:grid;
    justify-items:end;
    gap:5px;
    margin-left:auto;
  }
  .eoc-agent-actions .agent-actions-menu{
    margin:0;
  }
  .eoc-agent-actions .action-help{
    max-width:420px;
    margin:0;
    text-align:right;
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
      display:grid;
      grid-template-columns:1fr;
      align-items:stretch;
      gap:10px;
      margin-bottom:18px;
    }
    .eoc-agent-context-row .agent-picker{
      width:100%;
    }
    .eoc-agent-actions{
      width:100%;
      margin-left:0;
      justify-items:stretch;
    }
    .eoc-agent-actions .agent-actions-menu,
    .eoc-agent-actions .agent-actions-menu>summary{
      width:100%;
    }
    .eoc-agent-actions .agent-actions-menu>div{
      left:0;
      right:0;
    }
    .eoc-agent-actions .action-help{
      max-width:none;
      text-align:left;
    }
  }
`;

export function applyManagementToolbarLayout(panel) {
  const root = panel?.shadowRoot;
  if (!root) return false;

  if (!root.querySelector("style[data-eoc-management-toolbar]")) {
    const style = document.createElement("style");
    style.dataset.eocManagementToolbar = "";
    style.textContent = TOOLBAR_STYLE;
    root.append(style);
  }

  const header = root.querySelector("header");
  const topNav = root.querySelector(".top-nav");
  const agentPicker = root.querySelector(".agent-picker");
  const settingsSearch = root.querySelector(".eoc-global-search");
  const actionsMenu = root.querySelector("main .agent-actions-menu") || root.querySelector(".agent-actions-menu");
  const actionHelp = root.querySelector("main .action-help") || root.querySelector(".action-help");
  if (!header || !topNav || (!agentPicker && !settingsSearch)) return false;

  if (settingsSearch && settingsSearch.parentElement !== header) header.append(settingsSearch);

  if (agentPicker) {
    let contextRow = root.querySelector(".eoc-agent-context-row");
    if (!contextRow) {
      contextRow = document.createElement("div");
      contextRow.className = "eoc-agent-context-row";
      contextRow.setAttribute("aria-label", "Assistant context");
      topNav.before(contextRow);
    }
    if (agentPicker.parentElement !== contextRow) contextRow.prepend(agentPicker);

    if (actionsMenu) {
      const summary = actionsMenu.querySelector("summary");
      if (summary && summary.textContent !== "Assistant actions") summary.textContent = "Assistant actions";
      let actionGroup = contextRow.querySelector(".eoc-agent-actions");
      if (!actionGroup) {
        actionGroup = document.createElement("div");
        actionGroup.className = "eoc-agent-actions";
        contextRow.append(actionGroup);
      }
      if (actionsMenu.parentElement !== actionGroup) {
        actionGroup.replaceChildren(actionsMenu);
        if (actionHelp) actionGroup.append(actionHelp);
      }
      const configToolbar = root.querySelector(".config-toolbar");
      if (configToolbar && !configToolbar.children.length) configToolbar.remove();
    }

  }

  const subsectionNav = root.querySelector(".subsection-nav");
  if (subsectionNav && topNav.nextElementSibling !== subsectionNav) topNav.after(subsectionNav);
  return true;
}

export {TOOLBAR_STYLE};
