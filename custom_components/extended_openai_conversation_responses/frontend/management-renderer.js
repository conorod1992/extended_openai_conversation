import {refreshSettingEffects} from "./management-configuration-clarity.js";
import {updateDialogs} from "./management-dialogs.js";
import {NAVIGATION, pageMetadata, searchSettings, shouldShowGlobalSettingsSearch} from "./frontend-navigation.js";

const SEARCH_DEBOUNCE_MS = 80;

function navigationFor(panel) {
  return NAVIGATION.filter((item) => panel._canAccessView(item.id));
}

function settingsItems(panel, query = panel._settingsSearchQuery) {
  return searchSettings(query).filter((item) => panel._canAccessView(item.page, item.section));
}

export function settingsResultsMarkup(panel, query = panel._settingsSearchQuery) {
  if (!String(query || "").trim()) return "";
  const items = settingsItems(panel, query);
  return items.map((item) => `<button type="button" class="settings-result" role="option" data-page="${item.page}" data-subsection="${item.section}" data-target="${item.target || ""}"><strong>${panel._e(item.label)}</strong><span>${panel._e(pageMetadata(item.page).label)} › ${panel._e(pageMetadata(item.page).sections.find((section) => section.id === item.section)?.label || "")}</span><small>${panel._e(item.description)}</small></button>`).join("") || `<p class="empty">No settings match.</p>`;
}

function setDependent(root, key, enabled) {
  if (!key) return;
  const container = root.querySelector(`[data-dependent="${CSS.escape(key)}"]`);
  if (!container) return;
  container.classList.toggle("is-disabled", !enabled);
  container.querySelectorAll("input:not([readonly]),select,textarea:not([readonly]),button:not(.help-button)").forEach((control) => {
    control.disabled = !enabled;
  });
}

function controlValue(control) {
  let value = control.dataset?.type === "boolean" ? Boolean(control.checked) : control.value;
  if (control.dataset?.type === "number") value = Number(value);
  const key = control.dataset?.config || "";
  if (key === "skills" || key.startsWith("guest_readable_") || key.startsWith("guest_controllable_")) {
    value = String(value).split(",").map((item) => item.trim()).filter(Boolean);
  }
  return value;
}

function hasClass(control, name) {
  return Boolean(control.classList?.contains?.(name));
}

export function applyIncrementalDraftUpdate(panel, control, root = panel.shadowRoot) {
  if (!panel._draft || !control) return false;

  const key = control.dataset?.config;
  if (key) {
    if (key === "__title") panel._draftTitle = control.value;
    else if (key === "prompt") panel._draft.prompt = control.value;
    else panel._draft[key] = controlValue(control);
    return true;
  }

  if (control.id === "voice-mappings") {
    try {
      panel._draft.voice_device_mappings = JSON.parse(control.value || "{}");
    } catch (_) {
      panel._draft.voice_device_mappings = control.value;
    }
    return true;
  }

  if (control.dataset?.localIntentExclusion !== undefined) {
    panel._draft.local_intent_exclusions = [...root.querySelectorAll("[data-local-intent-exclusion]:checked")].map((input) => input.value);
    return true;
  }

  if (hasClass(control, "regex-pattern") || hasClass(control, "regex-replacement")) {
    const row = control.closest?.(".rule-row");
    const index = Number(row?.dataset?.regexIndex);
    if (!Number.isInteger(index) || index < 0 || !panel._draft.speech_regex_replacements?.[index]) return false;
    const rule = panel._draft.speech_regex_replacements[index];
    if (hasClass(control, "regex-pattern")) rule.pattern = control.value;
    else rule.replacement = control.value;
    return true;
  }

  return false;
}

function bindIncrementalDraftUpdates(panel) {
  const root = panel.shadowRoot;
  if (root.__eocIncrementalDraftBound) return;
  root.__eocIncrementalDraftBound = true;
  root.addEventListener("input", (event) => {
    // Let the existing editor perform its full read once when a clean draft first
    // becomes dirty. From then on, keep the already-hydrated draft current by
    // updating only the control that changed.
    if (!panel._configDirty || !root.querySelector(".save-bar")) return;
    const control = event.target;
    if (!applyIncrementalDraftUpdate(panel, control, root)) return;

    event.stopImmediatePropagation();
    if (control.dataset?.type === "boolean") setDependent(root, control.dataset.config, control.checked);
    if (control.dataset?.config === "conversation_continuity") {
      setDependent(root, "conversation_continuity", control.value !== "ha_default");
    }
    if (control.id === "prompt-editor") {
      const counter = root.querySelector("#prompt-count");
      if (counter) counter.textContent = `${control.value.length.toLocaleString()} characters`;
    }
    // The optimized handler stops the original event before bubble listeners,
    // so reconcile this control explicitly after updating the shared draft.
    panel._syncConfigControlDirty?.(control);
    refreshSettingEffects(panel, control);
  }, true);
}

function ensureHost(parent, id, before) {
  let host = parent.querySelector(`#${id}`);
  if (!host) {
    host = document.createElement("div");
    host.id = id;
    parent.insertBefore(host, before);
  }
  return host;
}

function preparePersistentShell(panel) {
  const root = panel.shadowRoot;
  const shell = root.querySelector(".page-shell");
  const layout = shell?.querySelector(".section-layout");
  if (!shell || !layout) return false;

  shell.dataset.eocPersistentShell = "";
  root.querySelector("style")?.setAttribute("data-eoc-persistent-styles", "");
  layout.querySelector("main")?.setAttribute("data-eoc-main", "");

  const settingsHost = ensureHost(shell, "eoc-settings-host", layout);
  const scopeHost = ensureHost(shell, "eoc-scope-host", layout);
  const sectionHost = ensureHost(shell, "eoc-section-host", layout);
  const existingSettings = [...shell.children].find((item) => item.classList?.contains("global-search"));
  const existingScope = [...shell.children].find((item) => item.classList?.contains("scope-bar"));
  const existingSection = [...shell.children].find((item) => item.classList?.contains("section-selector"));
  if (existingSettings) settingsHost.append(existingSettings);
  if (existingScope) scopeHost.append(existingScope);
  if (existingSection) sectionHost.append(existingSection);

  let dialogHost = root.querySelector("#eoc-dialog-host");
  if (!dialogHost) {
    dialogHost = document.createElement("div");
    dialogHost.id = "eoc-dialog-host";
    const toast = root.querySelector("#toast");
    root.insertBefore(dialogHost, toast || null);
    [...root.children].filter((item) => item.tagName === "DIALOG").forEach((dialog) => dialogHost.append(dialog));
  }

  panel._eocPersistentReady = true;
  bindDynamicBase(panel);
  bindIncrementalDraftUpdates(panel);
  bindSettingsSearch(panel, true);
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
    detail.textContent = `${agent.provider} · ${agent.model}`;
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

function bindSettingsResultButtons(panel) {
  panel.shadowRoot.querySelectorAll("#eoc-settings-host .settings-result").forEach((button) => {
    if (button.dataset.eocBound) return;
    button.dataset.eocBound = "";
    button.addEventListener("click", async () => {
      panel._pendingSettingFocus = button.dataset.target;
      panel._settingsSearchQuery = "";
      await panel._navigate(button.dataset.page, button.dataset.subsection);
    });
  });
}

function updateSettingsResults(panel) {
  const root = panel.shadowRoot;
  const search = root.querySelector("#eoc-settings-host .global-search");
  if (!search) return;
  const query = panel._settingsSearchQuery || "";
  let results = search.querySelector(".search-results");
  if (!query.trim()) {
    results?.remove();
    return;
  }
  if (!results) {
    results = document.createElement("div");
    results.className = "search-results";
    results.setAttribute("role", "listbox");
    results.setAttribute("aria-label", "Settings search results");
    search.append(results);
  }
  results.innerHTML = settingsResultsMarkup(panel, query);
  bindSettingsResultButtons(panel);
}

function bindSettingsSearch(panel, stripLegacyListener = false) {
  const root = panel.shadowRoot;
  let input = root.querySelector("#eoc-settings-host #settings-search");
  if (!input) return;
  if (stripLegacyListener && !input.dataset.eocSearchBound) {
    const replacement = input.cloneNode(true);
    input.replaceWith(replacement);
    input = replacement;
  }
  if (input.dataset.eocSearchBound) return;
  input.dataset.eocSearchBound = "";
  input.addEventListener("input", () => {
    panel._settingsSearchQuery = input.value;
    clearTimeout(panel._eocSettingsSearchTimer);
    panel._eocSettingsSearchTimer = setTimeout(() => updateSettingsResults(panel), SEARCH_DEBOUNCE_MS);
  });
}

function updateSettingsHost(panel) {
  const root = panel.shadowRoot;
  const host = root.querySelector("#eoc-settings-host");
  if (!host) return;
  const visible = shouldShowGlobalSettingsSearch(panel._page, panel._subsection);
  host.hidden = !visible;
  if (!visible) return;
  if (!host.querySelector(".global-search")) {
    host.innerHTML = `<div class="global-search"><label><span class="sr-only">Search all settings</span><input id="settings-search" type="search" value="${panel._e(panel._settingsSearchQuery)}" placeholder="Search all settings" aria-label="Search all settings"></label></div>`;
  }
  const input = host.querySelector("#settings-search");
  if (input && document.activeElement !== input && input.value !== panel._settingsSearchQuery) input.value = panel._settingsSearchQuery || "";
  bindSettingsSearch(panel);
  updateSettingsResults(panel);
}

function bindDynamicBase(panel) {
  const root = panel.shadowRoot;
  if (!root.__eocRouteControlsBound) {
    root.__eocRouteControlsBound = true;
    root.addEventListener("change", async (event) => {
      const control = event.target;
      if (control?.id === "local-section") void panel._navigate(panel._page, control.value);
      if (control?.id === "scope") {
        const nextScope = control.value;
        control.value = panel._scopeId;
        if (!await panel._confirmUnsavedNavigation(panel._viewKey())) return;
        panel._scopeId = nextScope;
        control.value = nextScope;
        void panel._loadSection();
      }
      if (control?.id === "show-empty-scopes") {
        panel._showEmptyScopes = control.checked;
        panel._render();
      }
    });
  }
  bindSettingsResultButtons(panel);
}

const regionMarkup = new WeakMap();
function updateRegion(host, markup) {
  if (!host || regionMarkup.get(host) === markup) return;
  host.innerHTML = markup;
  regionMarkup.set(host, markup);
}

function bindDynamicMain(panel) {
  const root = panel.shadowRoot;
  root.querySelectorAll("main .inline-route").forEach((button) => button.addEventListener("click", () => panel._navigate(button.dataset.page, button.dataset.subsection)));
  root.querySelectorAll("main .guide-topic-link").forEach((button) => button.addEventListener("click", () => {
    panel._guideTopic = button.dataset.guideTopic;
    panel._navigate("guide");
  }));
}

function renderDynamicRegions(panel) {
  const root = panel.shadowRoot;
  const agent = panel._selectedAgent();
  const local = panel._visibleSubsections();
  const currentSection = local.find((item) => item.id === panel._subsection);

  updateAgentPicker(panel, agent);
  updateNavigation(panel, navigationFor(panel));
  updateSettingsHost(panel);

  const scopeHost = root.querySelector("#eoc-scope-host");
  updateRegion(scopeHost, ["data-memory/conversations", "data-memory/memories"].includes(panel._viewKey()) ? panel._scopePicker() : "");

  const sectionHost = root.querySelector("#eoc-section-host");
  if (sectionHost) {
    updateRegion(sectionHost, local.length > 1 ? `<div class="section-selector"><label><span>${panel._e(pageMetadata(panel._page).label)} section</span><select id="local-section">${local.map((item) => `<option value="${item.id}" ${item.id === panel._subsection ? "selected" : ""}>${item.label}</option>`).join("")}</select></label><p>${panel._e(currentSection?.description || "")}</p></div>` : "");
  }

  const main = root.querySelector("[data-eoc-main]") || root.querySelector("main");
  const markup = !agent
    ? panel._empty("No conversation agents configured.")
    : panel._busy ? panel._loading()
      : panel._error ? `<div class="error" role="alert">${panel._e(panel._error)}</div>`
        : panel._content(agent);
  const dialogs = panel._dialogs();
  const route = `${panel._agentId}|${panel._viewKey()}`;
  const changed = route !== panel._eocRenderedRoute || markup !== panel._eocMainMarkup
    || dialogs !== panel._eocDialogMarkup;
  if (main && changed && route === panel._eocRenderedRoute && root.querySelector("dialog[open]")) {
    panel._eocDeferredEditorRender = true;
    return;
  }
  panel._eocDeferredEditorRender = false;
  if (main && changed) {
    main.innerHTML = markup;
    panel._eocMainMarkup = markup;
    panel._eocDialogMarkup = dialogs;
    panel._eocRenderedRoute = route;
    updateDialogs(panel, dialogs);
    panel._bindActions();
    bindDynamicMain(panel);
  }

  bindDynamicBase(panel);
  bindIncrementalDraftUpdates(panel);
}

// The host calls this directly; feature decorators cannot own shell lifetime.
export function renderManagement(panel) {
  const navigation = navigationFor(panel);
  if (!panel._eocPersistentReady || !panel.shadowRoot.querySelector("[data-eoc-persistent-shell]")
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
  if (!main) return false;
  main.innerHTML = panel._loading?.() || '<div class="loading" role="status">Loading…</div>';
  main.setAttribute("aria-busy", "true");
  main.dataset.eocInitialLoading = "";
  return true;
}

