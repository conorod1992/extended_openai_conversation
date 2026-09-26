import {updateDialogs} from "./management-dialogs.js";
import {NAVIGATION, pageMetadata} from "./frontend-navigation.js";

export const ASSISTANT_INTRO_MARKUP = '<section class="page-intro assistant-parent-intro"><h1>Assistant settings</h1><p>Configure how this assistant responds, handles conversations, uses context, and works with voice.</p></section>';

function navigationFor(panel) {
  return NAVIGATION.filter((item) => panel._canAccessView(item.id));
}

function preparePersistentShell(panel) {
  const root = panel.shadowRoot;
  const shell = root.querySelector("[data-eoc-persistent-shell]");
  const main = shell?.querySelector("[data-eoc-main]");
  const dialogHost = root.querySelector("#eoc-dialog-host");
  if (!shell || !main || !dialogHost) return false;

  bindDynamicBase(panel);
  return true;
}

function navigationMatches(panel, navigation) {
  const buttons = [...panel.shadowRoot.querySelectorAll(".top-nav button")];
  return buttons.length === navigation.length && buttons.every((button, index) => button.dataset.page === navigation[index].id);
}

function updateAgentPicker(panel, agent) {
  const root = panel.shadowRoot;
  const picker = root.querySelector(".agent-picker");
  const select = picker?.querySelector("#agent");
  if (!picker || !select) return;
  const agents = panel._data?.agents || [];
  const signature = agents.map((item) => `${item.subentry_id}:${item.title}`).join("|");
  if (select.dataset.eocAgentSignature !== signature) {
    select.innerHTML = agents.map((item) => `<option value="${panel._e(item.subentry_id)}">${panel._e(item.title)}</option>`).join("");
    select.dataset.eocAgentSignature = signature;
  }
  select.value = panel._agentId || "";
  let detail = picker.querySelector("small");
  if (agent) {
    if (!detail) {
      detail = document.createElement("small");
      picker.append(detail);
    }
    if (!detail.textContent) detail.textContent = `${agent.provider} · ${agent.model}`;
  } else {
    detail?.remove();
  }
}

function updateNavigation(panel, navigation) {
  const root = panel.shadowRoot;
  root.querySelectorAll(".top-nav button").forEach((button) => {
    const active = button.dataset.page === panel._page;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const mobile = root.querySelector("#top-section-mobile");
  if (mobile) mobile.value = panel._page;
}

// A pending route keeps its previous main content, but its destination controls
// must acknowledge the navigation before either code or data arrives.
export function showPendingDestination(panel) {
  const root = panel.shadowRoot;
  if (!root?.querySelector("[data-eoc-persistent-shell]")) return;
  updateNavigation(panel, navigationFor(panel));
  const local = panel._visibleSubsections();
  const nav = root.querySelector(".subsection-nav");
  if (nav) {
    const markup = local.map((item) => `<button type="button" data-subsection="${panel._e(item.id)}" class="${item.id === panel._subsection ? "active" : ""}" ${item.id === panel._subsection ? 'aria-current="page"' : ""}>${panel._e(item.label)}</button>`).join("");
    if (nav._eocMarkup !== markup) {
      nav.innerHTML = markup;
      nav._eocMarkup = markup;
    }
    nav.hidden = local.length <= 1;
    nav.setAttribute("aria-label", `${pageMetadata(panel._page).label} sections`);
  }
  const sectionHost = root.querySelector("#eoc-section-host");
  updateRegion(sectionHost, local.length > 1 ? `<div class="section-selector"><label><span>${panel._e(pageMetadata(panel._page).label)} section</span><select id="local-section" aria-description="${panel._e(local.find((item) => item.id === panel._subsection)?.description || "")}">${local.map((item) => `<option value="${panel._e(item.id)}" ${item.id === panel._subsection ? "selected" : ""}>${panel._e(item.label)}</option>`).join("")}</select></label></div>` : "");
  const main = root.querySelector("[data-eoc-main]");
  if (main) {
    main.setAttribute("aria-busy", "true");
    main.inert = true;
    main.classList.add("eoc-loading-in-background");
  }
}

function bindDynamicBase(panel) {
  const root = panel.shadowRoot;
  if (root.__eocRouteControlsBound) return;
  root.__eocRouteControlsBound = true;

  root.addEventListener("click", (event) => {
    const target = event.target;
    const pageButton = target?.closest?.(".top-nav button[data-page]");
    if (pageButton) {
      void panel._navigate(pageButton.dataset.page);
      return;
    }
    const subsectionButton = target?.closest?.(".subsection-nav button[data-subsection]");
    if (subsectionButton) {
      void panel._navigate(panel._page, subsectionButton.dataset.subsection);
      return;
    }
    const routeButton = target?.closest?.(".inline-route");
    if (routeButton) {
      void panel._navigate(routeButton.dataset.page, routeButton.dataset.subsection);
      return;
    }
    const guideButton = target?.closest?.(".guide-topic-link");
    if (guideButton) {
      panel._guideTopic = guideButton.dataset.guideTopic;
      void panel._navigate("guide");
    }
  });

  root.addEventListener("change", async (event) => {
    const control = event.target;
    if (control?.id === "top-section-mobile") {
      void panel._navigate(control.value);
      return;
    }
    if (control?.id === "local-section") {
      void panel._navigate(panel._page, control.value);
      return;
    }
    if (control?.id === "agent") {
      const nextAgent = control.value;
      control.value = panel._agentId;
      if (!await panel._confirmUnsavedNavigation(null)) return;
      control.value = nextAgent;
      panel._unsavedState?.scopes.clear();
      panel._agentId = nextAgent;
      localStorage.setItem("extended-openai-agent", panel._agentId);
      panel._clearConfigDraft();
      panel._scopeId = null;
      panel._applyScopes(panel._scopeCatalogCache.get(panel._scopeCatalogKey()) || panel._baseScopes);
      await panel._loadSection();
      return;
    }
    if (control?.id === "scope") {
      const nextScope = control.value;
      control.value = panel._scopeId;
      if (!await panel._confirmUnsavedNavigation(panel._viewKey())) return;
      panel._scopeId = nextScope;
      control.value = nextScope;
      void panel._loadSection();
      return;
    }
    if (control?.id === "show-empty-scopes") {
      panel._showEmptyScopes = control.checked;
      panel._render();
    }
  });
}

const regionMarkup = new WeakMap();
function updateRegion(host, markup) {
  if (!host || regionMarkup.get(host) === markup) return;
  const active = host.contains(host.getRootNode().activeElement)
    ? host.getRootNode().activeElement?.id : null;
  host.innerHTML = markup;
  regionMarkup.set(host, markup);
  if (active) host.querySelector(`#${CSS.escape(active)}`)?.focus({preventScroll: true});
}

function updateAgentActions(panel) {
  const host = panel.shadowRoot.querySelector("#eoc-agent-actions-host");
  if (!host) return;
  const markup = panel._selectedAgent() && !panel._busy && !panel._error
    ? panel._configurationActions?.() || ""
    : "";
  updateRegion(host, markup);
  host.hidden = !markup;
}

export function reconcileScopePicker(panel) {
  const host = panel.shadowRoot?.querySelector?.("#eoc-scope-host");
  if (!host) return false;
  const markup = panel._scopePicker();
  if (host.innerHTML === markup) return true;
  const activeId = host.contains(panel.shadowRoot.activeElement)
    ? panel.shadowRoot.activeElement?.id : null;
  updateRegion(host, markup);
  if (activeId) host.querySelector(`#${activeId}`)?.focus();
  return true;
}

export function reconcileHistoryConfiguration(panel, markup) {
  if (panel._viewKey?.() !== "data-memory/conversations" || panel._busy
      || panel.shadowRoot?.querySelector?.("dialog[open]")) return false;
  const host = panel.shadowRoot?.querySelector?.("[data-eoc-history-config]");
  if (!host) return false;
  host.innerHTML = markup;
  updateAgentActions(panel);
  const dialogs = panel._dialogs();
  updateDialogs(panel, dialogs, {preserveEditors: true});
  panel._eocDialogMarkup = dialogs;
  return true;
}

function renderDynamicRegions(panel) {
  const root = panel.shadowRoot;
  const agent = panel._selectedAgent();
  const local = panel._visibleSubsections();

  updateAgentPicker(panel, agent);
  updateNavigation(panel, navigationFor(panel));
  updateAgentActions(panel);

  const scopeHost = root.querySelector("#eoc-scope-host");
  updateRegion(scopeHost, ["data-memory/conversations", "data-memory/memories"].includes(panel._viewKey()) ? panel._scopePicker() : "");

  const sectionHost = root.querySelector("#eoc-section-host");
  if (sectionHost) {
    updateRegion(sectionHost, local.length > 1 ? `<div class="section-selector"><label><span>${panel._e(pageMetadata(panel._page).label)} section</span><select id="local-section" aria-description="${panel._e(local.find((item) => item.id === panel._subsection)?.description || "")}">${local.map((item) => `<option value="${panel._e(item.id)}" ${item.id === panel._subsection ? "selected" : ""}>${panel._e(item.label)}</option>`).join("")}</select></label></div>` : "");
  }

  const assistantIntroHost = root.querySelector("#eoc-assistant-intro-host");
  const assistantIntro = agent && panel._page === "assistant" ? ASSISTANT_INTRO_MARKUP : "";
  if (assistantIntroHost && assistantIntroHost.innerHTML !== assistantIntro) {
    updateRegion(assistantIntroHost, assistantIntro);
  }

  const main = root.querySelector("[data-eoc-main]") || root.querySelector("main");
  if (main) main.toggleAttribute("data-eoc-guide-layout", panel._page === "guide");
  const dialogs = panel._dialogs();
  const route = `${panel._agentId}|${panel._viewKey()}`;
  if (agent && !panel._busy && !panel._error && route === panel._eocRenderedRoute
      && !root.querySelector("dialog[open]") && panel._reconcileCollectionView?.()) {
    panel._eocDeferredEditorRender = false;
    if (dialogs !== panel._eocDialogMarkup) {
      updateDialogs(panel, dialogs, {preserveEditors: true});
      panel._eocDialogMarkup = dialogs;
    }
    return;
  }
  const markup = !agent
    ? panel._empty("No conversation agents configured.")
    : panel._busy ? (panel._loadingContent?.(agent) || panel._loading())
      : panel._error ? `<div class="error" role="alert">${panel._e(panel._error)}</div>`
        : panel._content(agent);
  const changed = route !== panel._eocRenderedRoute || markup !== panel._eocMainMarkup;
  const dialogsChanged = dialogs !== panel._eocDialogMarkup;
  if ((changed || dialogsChanged) && route === panel._eocRenderedRoute && root.querySelector("dialog[open]")) {
    panel._eocDeferredEditorRender = true;
    return;
  }
  panel._eocDeferredEditorRender = false;
  if (main && changed) {
    main.innerHTML = markup;
    delete main.dataset.eocInitialLoading;
    panel._eocMainRevision = (panel._eocMainRevision || 0) + 1;
    panel._eocMainMarkup = markup;
    panel._eocRenderedRoute = route;
    // Page bindings include feature dialog handlers, so refresh those editors here.
    updateDialogs(panel, dialogs);
    panel._eocDialogMarkup = dialogs;
    panel._bindActions();
  }

  if (!changed && dialogsChanged) {
    updateDialogs(panel, dialogs, {preserveEditors: true});
    panel._eocDialogMarkup = dialogs;
  }
}

// The host calls this directly; feature decorators cannot own shell lifetime.
export function renderManagement(panel) {
  const navigation = navigationFor(panel);
  if (!panel.shadowRoot.querySelector("[data-eoc-persistent-shell]")
      || !navigationMatches(panel, navigation)) {
    panel._renderShell();
    preparePersistentShell(panel);
    showInitialLoading(panel);
    return;
  }
  renderDynamicRegions(panel);
  showInitialLoading(panel);
}

export function showInitialLoading(panel) {
  if (panel?._data !== null) return false;
  const main = panel.shadowRoot?.querySelector?.("main");
  if (!main || main.dataset.eocInitialLoading !== undefined) return false;
  main.innerHTML = panel._loading?.() || '<div class="loading" role="status">Loading…</div>';
  panel._eocMainRevision = (panel._eocMainRevision || 0) + 1;
  main.setAttribute("aria-busy", "true");
  main.dataset.eocInitialLoading = "";
  return true;
}
