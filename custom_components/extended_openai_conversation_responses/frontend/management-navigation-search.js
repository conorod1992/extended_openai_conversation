import {pageMetadata, searchSettings} from "./frontend-navigation.js";

const PATCHED = Symbol.for("extended-openai.management-navigation-search");
const SEARCH_STYLE = `
  .global-search.eoc-global-search{margin:0 0 22px;position:relative}
  .eoc-global-search>label{display:grid;grid-template-columns:auto minmax(220px,520px);gap:12px;align-items:center}
  .eoc-global-search .search-label{font-weight:600;color:var(--primary-text-color)}
  .eoc-global-search .search-results{margin-top:8px;max-width:760px;max-height:min(60vh,560px);overflow:auto;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.16);padding:6px;display:grid;gap:4px;position:absolute;z-index:20;left:0;right:auto;width:min(760px,100%)}
  .eoc-global-search .settings-result{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 16px;text-align:left;background:transparent;color:var(--primary-text-color);border-radius:8px;padding:11px 12px;min-height:0}
  .eoc-global-search .settings-result:hover,.eoc-global-search .settings-result:focus-visible{background:var(--secondary-background-color)}
  .eoc-global-search .settings-result strong{font-size:14px}
  .eoc-global-search .settings-result .setting-path{font-size:12px;color:var(--secondary-text-color);text-align:right}
  .eoc-global-search .settings-result small{grid-column:1;color:var(--secondary-text-color);line-height:1.4}
  .eoc-global-search .settings-current{grid-column:2;grid-row:2;font-size:12px;color:var(--primary-color);align-self:start;text-align:right;white-space:nowrap}
  .eoc-global-search .settings-loading{color:var(--secondary-text-color)}
  .eoc-global-search .empty{margin:8px 10px}
  .subsection-nav{display:flex;gap:8px;flex-wrap:wrap;overflow:visible;border:0;margin:0 0 12px;padding:0}
  .subsection-nav button{min-height:38px;padding:8px 13px;border:1px solid var(--divider-color);border-radius:999px;background:var(--card-background-color);color:var(--secondary-text-color)}
  .subsection-nav button.active{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color));color:var(--primary-color)}
  @media (min-width:801px){.section-selector>label{display:none}.section-selector{margin-top:0}.subsection-nav+.section-selector{margin-top:0}}
  @media (max-width:800px){.subsection-nav{display:none}.eoc-global-search>label{grid-template-columns:1fr}.eoc-global-search .search-label{display:none}.eoc-global-search .search-results{position:static;max-height:52vh;width:100%}.eoc-global-search .settings-result{grid-template-columns:1fr}.eoc-global-search .settings-result .setting-path,.eoc-global-search .settings-current{grid-column:1;text-align:left}.eoc-global-search .settings-result small{grid-column:1}}
`;

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function activeConfiguration(panel, explicitData = null) {
  if (panel._draft && panel._draftAgentId === panel._agentId) {
    return {data: panel._configData || explicitData || {}, config: panel._draft, title: panel._draftTitle, draft: true};
  }
  const cached = panel._settingsSearchConfigAgentId === panel._agentId ? panel._settingsSearchConfig : null;
  const data = explicitData || cached || panel._configData;
  if (!data?.config) return null;
  return {data, config: data.config, title: data.title, draft: false};
}

function optionLabel(data, key, value) {
  const option = data?.options?.[key]?.find((item) => String(typeof item === "string" ? item : item.value) === String(value));
  if (!option) return null;
  return typeof option === "string" ? titleCase(option) : option.label;
}

function countLabel(value, singular, plural) {
  let count = 0;
  if (Array.isArray(value)) count = value.length;
  else if (typeof value === "string") count = value.split(",").map((item) => item.trim()).filter(Boolean).length;
  else if (value && typeof value === "object") count = Object.keys(value).length;
  return `${count} ${count === 1 ? singular : plural}`;
}

export function settingCurrentState(item, panel, explicitData = null) {
  if (item.source === "guest-mode") {
    const state = panel?._selectedAgent?.()?.guest_mode?.state;
    return state ? {label:"Current", value:titleCase(state)} : null;
  }
  if (!item.configKey) return null;

  const active = activeConfiguration(panel, explicitData);
  if (!active) return null;
  if (item.capability && active.data?.model_capabilities?.[item.capability] === false) {
    return {label:"Current", value:"Not supported by current model"};
  }

  const value = item.configKey === "__title" ? active.title : active.config?.[item.configKey];
  if (value === undefined) return null;
  let display;
  const choice = optionLabel(active.data, item.configKey, value);
  if (choice) display = choice;
  else if (item.format === "boolean") display = value ? "On" : "Off";
  else if (item.format === "template") display = String(value || "").trim() ? "Custom" : "Default";
  else if (item.format === "prompt") display = `${String(value || "").length.toLocaleString()} characters`;
  else if (item.format === "mapping") display = countLabel(value, "assignment", "assignments");
  else if (item.format === "count") display = countLabel(value, item.singular || "item", item.plural || "items");
  else if (item.format === "text") display = String(value || "").trim() || "Not set";
  else if (typeof value === "boolean") display = value ? "On" : "Off";
  else if (Array.isArray(value)) display = countLabel(value, "item", "items");
  else display = `${value ?? "Not set"}${item.suffix || ""}`;
  return {label:active.draft ? "Current draft" : "Current", value:String(display)};
}

function visibleSettings(panel) {
  return searchSettings(panel._settingsSearchQuery).filter((item) => {
    if (!panel._canAccessView(item.page, item.section)) return false;
    if (item.configKey && panel._data?.is_admin === false) return false;
    return true;
  });
}

function pathLabel(item) {
  const page = pageMetadata(item.page);
  const section = page.sections.find((candidate) => candidate.id === item.section);
  return `${page.label}${section ? ` › ${section.label}` : ""}`;
}

function searchMarkup(panel) {
  const query = String(panel._settingsSearchQuery || "");
  const results = visibleSettings(panel);
  const configLoading = Boolean(query && panel._data?.is_admin !== false && !activeConfiguration(panel) && panel._settingsSearchConfigLoading);
  return `<div class="global-search eoc-global-search"><label><span class="search-label">Find a setting</span><input id="settings-search" type="search" value="${panel._e(query)}" placeholder="Search settings by name or purpose" aria-label="Search all settings" autocomplete="off"></label>${query ? `<div class="search-results" role="listbox" aria-label="Settings search results">${results.map((item) => {
    const state = settingCurrentState(item, panel);
    const current = state
      ? `<span class="settings-current">${panel._e(state.label)}: ${panel._e(state.value)}</span>`
      : item.configKey && configLoading ? '<span class="settings-current settings-loading">Current value loading…</span>' : "";
    return `<button type="button" class="settings-result" role="option" data-page="${panel._e(item.page)}" data-subsection="${panel._e(item.section)}" data-target="${panel._e(item.target || "")}"><strong>${panel._e(item.label)}</strong><span class="setting-path">${panel._e(pathLabel(item))}</span><small>${panel._e(item.description)}</small>${current}</button>`;
  }).join("") || '<p class="empty">No settings match.</p>'}</div>` : ""}</div>`;
}

async function ensureSearchConfiguration(panel) {
  if (panel._data?.is_admin === false || !panel._settingsSearchQuery || activeConfiguration(panel)) return;
  if (!visibleSettings(panel).some((item) => item.configKey)) return;
  const agentId = panel._agentId;
  if (!agentId) return;
  if (panel._settingsSearchConfigAgentId === agentId && panel._settingsSearchConfig?.config) return;
  if (panel._settingsSearchConfigPromise && panel._settingsSearchConfigPromiseAgentId === agentId) return panel._settingsSearchConfigPromise;

  panel._settingsSearchConfigLoading = true;
  panel._settingsSearchConfigPromiseAgentId = agentId;
  panel._settingsSearchConfigPromise = panel._call("configuration", "get")
    .then((data) => {
      if (panel._agentId !== agentId) return;
      panel._settingsSearchConfig = data;
      panel._settingsSearchConfigAgentId = agentId;
      if (panel._settingsSearchQuery) {
        panel._settingsSearchShouldFocus = true;
        panel._render();
      }
    })
    .catch(() => {})
    .finally(() => {
      if (panel._settingsSearchConfigPromiseAgentId === agentId) {
        panel._settingsSearchConfigLoading = false;
        panel._settingsSearchConfigPromise = null;
        panel._settingsSearchConfigPromiseAgentId = null;
      }
    });
  return panel._settingsSearchConfigPromise;
}

function bindSearch(panel) {
  const root = panel.shadowRoot;
  const input = root.querySelector("#settings-search");
  input?.addEventListener("input", (event) => {
    panel._settingsSearchQuery = event.target.value;
    panel._settingsSearchShouldFocus = true;
    panel._render();
    void ensureSearchConfiguration(panel);
  });
  root.querySelectorAll(".settings-result").forEach((button) => button.addEventListener("click", async () => {
    panel._pendingSettingFocus = button.dataset.target;
    panel._settingsSearchQuery = "";
    await panel._navigate(button.dataset.page, button.dataset.subsection);
  }));
  if (panel._settingsSearchShouldFocus) {
    panel._settingsSearchShouldFocus = false;
    requestAnimationFrame(() => {
      const next = panel.shadowRoot.querySelector("#settings-search");
      next?.focus({preventScroll:true});
      next?.setSelectionRange(next.value.length, next.value.length);
    });
  }
}

function bindSubsectionNavigation(panel) {
  panel.shadowRoot.querySelectorAll(".subsection-nav button").forEach((button) => button.addEventListener("click", () => {
    void panel._navigate(panel._page, button.dataset.subsection);
  }));
}

function enhancePanel(panel) {
  const root = panel.shadowRoot;
  if (!root) return;
  root.querySelector("style[data-eoc-navigation-search]")?.remove();
  const style = document.createElement("style");
  style.dataset.eocNavigationSearch = "";
  style.textContent = SEARCH_STYLE;
  root.append(style);

  root.querySelector(".global-search")?.remove();
  const topNav = root.querySelector(".top-nav");
  if (topNav) topNav.insertAdjacentHTML("afterend", searchMarkup(panel));

  root.querySelector(".subsection-nav")?.remove();
  const local = panel._visibleSubsections();
  const selector = root.querySelector(".section-selector");
  if (selector && local.length > 1) {
    selector.insertAdjacentHTML("beforebegin", `<nav class="subsection-nav" aria-label="${panel._e(pageMetadata(panel._page).label)} sections">${local.map((item) => `<button type="button" data-subsection="${panel._e(item.id)}" class="${item.id === panel._subsection ? "active" : ""}" ${item.id === panel._subsection ? 'aria-current="page"' : ""}>${panel._e(item.label)}</button>`).join("")}</nav>`);
  }

  bindSearch(panel);
  bindSubsectionNavigation(panel);
  if (panel._settingsSearchQuery) void ensureSearchConfiguration(panel);
}

export function installManagementNavigationSearch(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      enhancePanel(this);
      return result;
    };

    const originalClearConfigDraft = prototype._clearConfigDraft;
    prototype._clearConfigDraft = function(...args) {
      const result = originalClearConfigDraft.apply(this, args);
      this._settingsSearchConfig = null;
      this._settingsSearchConfigAgentId = null;
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementNavigationSearch();
}

export {ensureSearchConfiguration, searchMarkup};
