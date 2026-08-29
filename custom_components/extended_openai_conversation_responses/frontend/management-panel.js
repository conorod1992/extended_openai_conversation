import { bindConfiguration, bindTools, configurationDialogs, renderConfiguration, renderTools, restoreDialog } from "./agent-config-editor.js";
import {NAVIGATION, pageMetadata, routeFromPath, routePath, searchSettings, shouldShowGlobalSettingsSearch} from "./frontend-navigation.js";
import {freshGuestPolicyDraft} from "./guest-mode-ui.js";
import {bindGuide, renderGuide} from "./guide-page.js";
import {bindOverview, renderOverview} from "./overview-page.js";
import {formatUsageNumber, formatUsageTimestamp, tokenBreakdown} from "./usage-chart.js";
import {bindRequestRules, renderRequestRules, requestRulesDialog} from "./request-rules-ui.js";

const WS_TYPE = "extended_openai_conversation_responses/management";
const KNOWLEDGE_TITLE_LIMIT = 120;
const KNOWLEDGE_DESCRIPTION_LIMIT = 500;
const KNOWLEDGE_LIMIT = 100000;

function settledSectionResult(entries, settled) {
  const result = {};
  const load_errors = [];
  entries.forEach(([key, label], index) => {
    const item = settled[index];
    if (item.status === "fulfilled") result[key] = item.value;
    else load_errors.push({key, label, message: item.reason?.message || String(item.reason || "Unknown error")});
  });
  return {...result, load_errors};
}

export class ExtendedOpenAIManagementPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const route = routeFromPath(window.location.pathname);
    this._page = route.page;
    this._subsection = route.section;
    this._data = null;
    this._result = null;
    this._busy = false;
    this._query = "";
    this._memoryKind = "persistent";
    this._showEmptyScopes = false;
    this._confirmResolver = null;
    this._configDirty = false;
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
    this._sectionCache = new Map();
    this._scopeCatalogCache = new Map();
    this._scopeCatalogVisitKey = null;
    this._baseScopes = [];
    this._serviceCatalog = null;
    this._serviceCatalogPromise = null;
    this._loadToken = 0;
    this._configSearchQuery = "";
    this._settingsSearchQuery = "";
    this._guideQuery = "";
    this._beforeUnloadHandler = (event) => {
      if (!this._configDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._loadAgents();
  }

  set route(value) {
    this._route = value;
    const route = routeFromPath(window.location.pathname);
    if (route.page !== this._page || route.section !== this._subsection) this._handleRouteChange(route);
    else if (!this.shadowRoot.hasChildNodes()) this._render();
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._beforeUnloadHandler);
  }

  _setConfigDirty(value) {
    const dirty = Boolean(value);
    if (dirty === this._configDirty) return;
    this._configDirty = dirty;
    const method = dirty ? "addEventListener" : "removeEventListener";
    window[method]("beforeunload", this._beforeUnloadHandler);
  }

  _clearConfigDraft() {
    this._setConfigDirty(false);
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
    this._configSearchQuery = "";
  }

  _syncConfigDirty() {
    const baseline = this._configData;
    this._setConfigDirty(Boolean(baseline) && (
      this._draftTitle !== baseline.title ||
      JSON.stringify(this._draft) !== JSON.stringify(baseline.config)
    ));
  }

  async _handleRouteChange(route) {
    if (this._isDraftView() && !this._isDraftView(route.page, route.section)) {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your configuration changes have not been saved.", "Discard");
        if (!discard) {
          history.pushState({}, "", routePath(this._page, this._subsection));
          return;
        }
      }
      this._clearConfigDraft();
    }
    this._page = route.page;
    this._subsection = route.section;
    this._query = "";
    this._result = null;
    await this._loadSection();
  }

  _viewKey(page = this._page, subsection = this._subsection) {
    return subsection ? `${page}/${subsection}` : page;
  }

  _isDraftView(page = this._page, subsection = this._subsection) {
    if (this._data && !this._data.is_admin) return false;
    return page === "assistant" ||
      (page === "capabilities" && ["home-assistant", "request-rules", "functions"].includes(subsection)) ||
      (page === "data-memory" && subsection === "conversations") ||
      (page === "usage-maintenance" && ["backup-restore", "retention"].includes(subsection));
  }

  _configSectionsForView() {
    return {
      "assistant/basics": ["general"],
      "assistant/model-responses": ["model"],
      "assistant/conversation": ["conversation", "context"],
      "assistant/prompt-context": ["prompt"],
      "assistant/voice": ["voice"],
      "assistant/speech": ["speech"],
      "assistant/advanced": ["capabilities"],
      "data-memory/conversations": ["archive"],
      "usage-maintenance/backup-restore": ["backup"],
      "usage-maintenance/retention": ["retention"],
    }[this._viewKey()] || [];
  }

  _canAccessView(page, subsection = null) {
    if (this._data?.is_admin !== false) return true;
    if (page === "assistant") return false;
    if (page === "capabilities" && subsection && subsection !== "guest-mode") return false;
    if (page === "usage-maintenance" && ["backup-restore", "retention"].includes(subsection)) return false;
    return true;
  }

  _visibleSubsections(page = this._page) {
    return pageMetadata(page).sections.filter((item) => this._canAccessView(page, item.id));
  }

  async _call(section, action, extra = {}) {
    if (!this._hass) return null;
    const agent = this._selectedAgent();
    const result = await this._hass.callWS({
      type: WS_TYPE,
      section,
      action,
      ...(agent ? { entry_id: agent.entry_id, subentry_id: agent.subentry_id } : {}),
      ...extra,
    });
    this._invalidateAfterMutation(agent?.subentry_id, section, action);
    return result;
  }

  _invalidateAfterMutation(agentId, section, action) {
    const mutations = {
      request_rules: new Set(["defaults", "wording_groups", "create", "update", "delete", "duplicate"]),
      knowledge: new Set(["create", "update", "delete"]),
      memories: new Set(["add", "update", "delete", "temporary_delete", "reassign_legacy"]),
      conversations: new Set(["delete"]),
    };
    if (!agentId || !mutations[section]?.has(action)) return;
    const prefix = `${agentId}|`;
    const view = {request_rules:"capabilities/request-rules", knowledge:"data-memory/knowledge"}[section];
    if (view) this._sectionCache.delete(`${prefix}${view}`);
    if (["memories", "conversations"].includes(section)) {
      for (const key of this._scopeCatalogCache.keys()) {
        if (key.startsWith(prefix)) this._scopeCatalogCache.delete(key);
      }
      if (this._scopeCatalogVisitKey?.startsWith(prefix)) this._scopeCatalogVisitKey = null;
    }
  }

  _sectionCacheKey(view = this._viewKey()) {
    const agentId = this._agentId;
    if (!agentId) return null;
    if (["capabilities/request-rules", "data-memory/knowledge"].includes(view)) return `${agentId}|${view}`;
    return null;
  }

  _scopeCatalogKey(view = this._viewKey(), agentId = this._agentId) {
    if (!agentId || !["data-memory/memories", "data-memory/conversations"].includes(view)) return null;
    return `${agentId}|${view}`;
  }

  _prepareScopeCatalogVisit(view) {
    const key = this._scopeCatalogKey(view);
    if (key !== this._scopeCatalogVisitKey) {
      this._scopeCatalogCache.clear();
      this._scopeCatalogVisitKey = key;
    }
    return key;
  }

  _applyScopes(scopes) {
    this._data.scopes = scopes || [];
    const current = this._data.scopes.find((scope) => scope.is_current_user);
    if (!this._data.scopes.some((scope) => scope.scope_id === this._scopeId)) {
      this._scopeId = current?.scope_id || this._data.scopes[0]?.scope_id;
    }
  }

  async _loadServiceCatalog() {
    if (this._serviceCatalog) return this._serviceCatalog;
    if (!this._serviceCatalogPromise) {
      this._serviceCatalogPromise = this._call("service_catalog", "get")
        .then((response) => {
          this._serviceCatalog = response?.services || {};
          return this._serviceCatalog;
        })
        .finally(() => { this._serviceCatalogPromise = null; });
    }
    return this._serviceCatalogPromise;
  }

  async _loadAgents(selectedId = null) {
    try {
      const previousAgentId = this._agentId;
      this._data = await this._hass.callWS({ type: WS_TYPE, action: "agents" });
      this._baseScopes = this._data.scopes || [];
      const saved = localStorage.getItem("extended-openai-agent");
      const agents = this._data.agents || [];
      const preferred = selectedId || saved;
      this._agentId = agents.some((item) => item.subentry_id === preferred) ? preferred : agents[0]?.subentry_id;
      if (this._agentId) localStorage.setItem("extended-openai-agent", this._agentId);
      if (previousAgentId !== this._agentId) this._scopeId = null;
      this._applyScopes(this._scopeCatalogCache.get(this._scopeCatalogKey()) || this._baseScopes);
      await this._loadSection();
    } catch (err) {
      this._error = err.message || String(err);
      this._render();
    }
  }

  async _loadScopes(scopeCatalogKey) {
    if (!this._selectedAgent() || !scopeCatalogKey) return;
    const agentId = this._agentId;
    if (this._scopeCatalogCache.has(scopeCatalogKey)) {
      this._applyScopes(this._scopeCatalogCache.get(scopeCatalogKey));
      return;
    }
    const response = await this._call("scopes", "catalog");
    const scopes = response.scopes || [];
    if (scopeCatalogKey !== this._scopeCatalogVisitKey || agentId !== this._agentId) return;
    this._scopeCatalogCache.set(scopeCatalogKey, scopes);
    this._applyScopes(scopes);
  }

  _selectedAgent() {
    return this._data?.agents?.find((item) => item.subentry_id === this._agentId);
  }

  async _loadSection(silent = false) {
    if (!this._selectedAgent()) return this._render();
    const view = this._viewKey();
    const loadToken = ++this._loadToken;
    const scopeCatalogKey = this._prepareScopeCatalogVisit(view);
    const configOnly = this._isDraftView() && view !== "data-memory/conversations" && !["capabilities/request-rules"].includes(view);
    if (configOnly && this._configData && this._draftAgentId === this._agentId) {
      this._contentData = null;
      this._result = this._configData;
      this._error = null;
      this._busy = false;
      this._render();
      return;
    }
    const needsScopes = ["data-memory/memories", "data-memory/conversations"].includes(view);
    let cacheKey = this._sectionCacheKey(view);
    if ((!needsScopes || this._scopeCatalogCache.has(scopeCatalogKey)) && cacheKey && this._sectionCache.has(cacheKey)) {
      this._contentData = null;
      this._result = this._sectionCache.get(cacheKey);
      this._error = null;
      this._busy = false;
      this._render();
      return;
    }
    if (!silent) {
      this._busy = true;
      this._render();
    }
    try {
      if (needsScopes) await this._loadScopes(scopeCatalogKey);
      if (loadToken !== this._loadToken) return;
      cacheKey = this._sectionCacheKey(view);
      let result;
      let contentData = null;
      if (cacheKey && this._sectionCache.has(cacheKey)) {
        result = this._sectionCache.get(cacheKey);
      } else if (view === "overview") {
        const entries = [["usage", "Usage"], ["conversations", "Conversation settings"], ["memories", "Memory"], ["knowledge", "Knowledge"]];
        const settled = await Promise.allSettled([
          this._call("usage", "summary"),
          this._call("conversations", "settings", { scope_id: this._scopeId }),
          this._call("memories", "list", { scope_id: this._scopeId, limit: 5 }),
          this._call("knowledge", "list"),
        ]);
        result = settledSectionResult(entries, settled);
      } else if (view === "usage-maintenance/usage") {
        const entries = [["summary", "Usage summary"], ["days", "Daily usage"], ["runs", "Recent runs"], ["retention", "Usage retention"]];
        const settled = await Promise.allSettled([
          this._call("usage", "summary"), this._call("usage", "daily"),
          this._call("usage", "runs", { limit: 30 }), this._call("usage", "retention"),
        ]);
        result = settledSectionResult(entries, settled);
      } else if (view === "data-memory/conversations") {
        const [sessions, settings, active] = await Promise.all([
          this._call("conversations", "list", { scope_id: this._scopeId, limit: 50 }),
          this._call("conversations", "settings", { scope_id: this._scopeId }),
          this._data?.is_admin ? this._call("conversations", "active") : Promise.resolve({active: []}),
        ]);
        contentData = { sessions, settings, active };
        if (this._data?.is_admin) await this._loadConfigDraft();
        else result = contentData;
      } else if (view === "data-memory/memories") {
        result = await this._call("memories", this._memoryKind === "temporary" ? "temporary_list" : "list", { scope_id: this._scopeId, limit: 100 });
      } else if (view === "data-memory/knowledge") {
        result = await this._call("knowledge", "list");
      } else if (view === "capabilities/guest-mode") {
        result = await this._call("guest_mode", "get");
        this._guestDraft = JSON.parse(JSON.stringify(result.config || {}));
        if (!result.legacy_policy) {
          this._guestMigrationReview = false;
          this._guestStartingFresh = false;
        }
      } else if (view === "capabilities/request-rules") {
        result = await this._call("request_rules", "list");
      } else if (this._isDraftView()) {
        await this._loadConfigDraft();
        result = this._configData;
      } else {
        result = null;
      }
      if (loadToken !== this._loadToken) return;
      this._contentData = contentData;
      if (!(this._isDraftView() && view === "data-memory/conversations" && this._data?.is_admin)) this._result = result;
      if (cacheKey && result !== undefined) this._sectionCache.set(cacheKey, result);
      this._error = null;
    } catch (err) {
      if (loadToken === this._loadToken) this._error = err.message || String(err);
    } finally {
      if (loadToken === this._loadToken) {
        this._busy = false;
        this._render();
      }
    }
  }

  async _loadConfigDraft() {
    if (!this._configData || this._draftAgentId !== this._agentId) {
      const agentId = this._agentId;
      const configData = await this._call("configuration", "get");
      if (agentId !== this._agentId) return;
      this._configData = configData;
      this._draft = JSON.parse(JSON.stringify(configData.config));
      this._draftTitle = configData.title;
      this._draftAgentId = agentId;
      this._setConfigDirty(false);
    }
    this._result = this._configData;
  }

  async _navigate(page, subsection = null) {
    const metadata = pageMetadata(page);
    const targetSubsection = subsection || this._visibleSubsections(page)[0]?.id || metadata.sections[0]?.id || null;
    if (this._isDraftView() && !this._isDraftView(page, targetSubsection)) {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your configuration changes have not been saved.", "Discard");
        if (!discard) return;
      }
      this._clearConfigDraft();
    }
    this._page = page;
    this._subsection = targetSubsection;
    this._query = "";
    history.pushState({}, "", routePath(page, targetSubsection));
    this._result = null;
    await this._loadSection();
  }

  _render() {
    const agent = this._selectedAgent();
    const navigation = NAVIGATION.filter((item) => this._canAccessView(item.id));
    const local = this._visibleSubsections();
    const currentSection = local.find((item) => item.id === this._subsection);
    const settingsView = shouldShowGlobalSettingsSearch(this._page, this._subsection);
    const settingsResults = searchSettings(this._settingsSearchQuery).filter((item) => this._canAccessView(item.page, item.section));
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <div class="page-shell">
        <header>
          <div class="page-heading"><h1>Extended OpenAI</h1><p>Configure your assistant, capabilities, retained data, and maintenance.</p></div>
          <label class="agent-picker"><span>Conversation agent</span><select id="agent">${(this._data?.agents || []).map((a) => `<option value="${this._e(a.subentry_id)}" ${a.subentry_id === this._agentId ? "selected" : ""}>${this._e(a.title)}</option>`).join("")}</select>${agent ? `<small>${this._e(agent.provider)} · ${this._e(agent.model)}</small>` : ""}</label>
        </header>
        <label class="mobile-nav"><span>Page</span><select id="top-section-mobile">${navigation.map((item) => `<option value="${item.id}" ${item.id === this._page ? "selected" : ""}>${item.label}</option>`).join("")}</select></label>
        <nav class="top-nav" aria-label="Management sections">${navigation.map((item) => `<button type="button" data-page="${item.id}" class="${item.id === this._page ? "active" : ""}" ${item.id === this._page ? 'aria-current="page"' : ""}>${item.label}</button>`).join("")}</nav>
        ${settingsView ? `<div class="global-search"><label><span class="sr-only">Search all settings</span><input id="settings-search" type="search" value="${this._e(this._settingsSearchQuery)}" placeholder="Search all settings" aria-label="Search all settings"></label>${this._settingsSearchQuery ? `<div class="search-results" role="listbox" aria-label="Settings search results">${settingsResults.map((item) => `<button type="button" class="settings-result" role="option" data-page="${item.page}" data-subsection="${item.section}" data-target="${item.target || ""}"><strong>${this._e(item.label)}</strong><span>${this._e(pageMetadata(item.page).label)} › ${this._e(pageMetadata(item.page).sections.find((section) => section.id === item.section)?.label || "")}</span><small>${this._e(item.description)}</small></button>`).join("") || `<p class="empty">No settings match.</p>`}</div>` : ""}</div>` : ""}
        ${["data-memory/conversations", "data-memory/memories"].includes(this._viewKey()) ? this._scopePicker() : ""}
        ${local.length > 1 ? `<div class="section-selector"><label><span>${this._e(pageMetadata(this._page).label)} section</span><select id="local-section">${local.map((item) => `<option value="${item.id}" ${item.id === this._subsection ? "selected" : ""}>${item.label}</option>`).join("")}</select></label><p>${this._e(currentSection?.description || "")}</p></div>` : ""}
        <div class="section-layout">
          <main>${!agent ? this._empty("No conversation agents configured.") : this._busy ? this._loading() : this._error ? `<div class="error" role="alert">${this._e(this._error)}</div>` : this._content(agent)}</main>
        </div>
      </div>
      ${this._dialogs()}
      <div id="toast" class="toast" role="status" aria-live="polite"></div>`;
    this._bindBase();
    this._bindActions();
  }

  _bindBase() {
    const root = this.shadowRoot;
    root.querySelectorAll(".top-nav button").forEach((button) => button.addEventListener("click", () => this._navigate(button.dataset.page)));
    root.querySelector("#top-section-mobile")?.addEventListener("change", (event) => this._navigate(event.target.value));
    root.querySelector("#local-section")?.addEventListener("change", (event) => this._navigate(this._page, event.target.value));
    root.querySelector("#settings-search")?.addEventListener("input", (event) => { this._settingsSearchQuery = event.target.value; this._render(); requestAnimationFrame(() => { const input = this.shadowRoot.querySelector("#settings-search"); input?.focus(); input?.setSelectionRange(input.value.length, input.value.length); }); });
    root.querySelectorAll(".settings-result").forEach((button) => button.addEventListener("click", async () => { this._pendingSettingFocus = button.dataset.target; this._settingsSearchQuery = ""; await this._navigate(button.dataset.page, button.dataset.subsection); }));
    root.querySelectorAll(".inline-route").forEach((button) => button.addEventListener("click", () => this._navigate(button.dataset.page, button.dataset.subsection)));
    root.querySelectorAll(".guide-topic-link").forEach((button) => button.addEventListener("click", () => { this._guideTopic = button.dataset.guideTopic; this._navigate("guide"); }));
    root.querySelector("#agent")?.addEventListener("change", async (event) => {
      if (this._configDirty) {
        const discard = await this._confirm("Discard unsaved changes?", "Your configuration changes have not been saved.", "Discard");
        if (!discard) { event.target.value = this._agentId; return; }
        this._clearConfigDraft();
      }
      this._agentId = event.target.value;
      localStorage.setItem("extended-openai-agent", this._agentId);
      this._clearConfigDraft();
      this._scopeId = null;
      this._applyScopes(this._scopeCatalogCache.get(this._scopeCatalogKey()) || this._baseScopes);
      await this._loadSection();
    });
    root.querySelector("#scope")?.addEventListener("change", (event) => { this._scopeId = event.target.value; this._loadSection(); });
    root.querySelector("#show-empty-scopes")?.addEventListener("change", (event) => { this._showEmptyScopes = event.target.checked; this._render(); });
    root.querySelector("#confirm-cancel")?.addEventListener("click", () => this._resolveConfirm(false));
    root.querySelector("#confirm-accept")?.addEventListener("click", () => this._resolveConfirm(true));
    root.querySelector("#confirm-dialog")?.addEventListener("cancel", (event) => { event.preventDefault(); this._resolveConfirm(false); });
  }

  _content(agent) {
    const view = this._viewKey();
    if (!this._canAccessView(this._page, this._subsection)) return this._empty("Administrator permission is required for this section.");
    if (view === "overview") return renderOverview(this, agent);
    if (view === "guide") return renderGuide(this);
    if (this._page === "assistant") { this._configSections = this._configSectionsForView(); return renderConfiguration(this); }
    if (view === "capabilities/home-assistant") return this._homeAssistant(agent);
    if (view === "capabilities/request-rules") return renderRequestRules(this);
    if (view === "capabilities/functions") return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="functions">What are Function Groups?</button>${renderTools(this)}`;
    if (view === "capabilities/guest-mode") return this._guestMode();
    if (view === "data-memory/memories") return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="memory">Learn about memory</button>${this._memories()}`;
    if (view === "data-memory/knowledge") return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="knowledge">Learn about Knowledge</button>${this._knowledge()}`;
    if (view === "data-memory/conversations") { this._configSections = ["archive"]; return `${this._conversations()}${this._data?.is_admin ? renderConfiguration(this) : ""}`; }
    if (view === "usage-maintenance/usage") return this._usage();
    if (view === "usage-maintenance/diagnostics") return this._diagnostics(agent);
    if (["usage-maintenance/backup-restore", "usage-maintenance/retention"].includes(view)) { this._configSections = this._configSectionsForView(); return renderConfiguration(this); }
    return this._empty("This section is not available.");
  }

  _homeAssistant(agent) {
    const contextIncluded = this._draft?.exposed_entities_enabled === true;
    return `<section class="page-intro"><h1>Home Assistant access</h1><p>Home Assistant controls which entities this assistant is allowed to access through Assist. Extended OpenAI can also automatically include exposed entity names and current states in the context sent to the model.</p></section><section class="content-card access-explainer"><div><h2>Entity access</h2><p>Home Assistant's Assist exposure settings decide which entities may be used by the assistant. Manage exposure in Home Assistant's voice assistant settings.</p></div><div class="compact-status"><span><strong>Include exposed entity states in the prompt</strong><small>Adds exposed entity names and current states to the context sent with each request. Turning this off does not necessarily prevent the assistant from using exposed entities through Home Assistant tools.</small></span><strong class="status-value ${contextIncluded ? "on" : ""}">${contextIncluded ? "On" : "Off"}</strong></div><button type="button" class="secondary inline-route" data-page="assistant" data-subsection="prompt-context">Configure exposed entity context</button></section><section class="notice"><strong>Guest Mode adds another boundary</strong><p>Guest Mode applies additional restrictions to the assistant's normal Home Assistant access.</p><button type="button" class="secondary inline-route" data-page="capabilities" data-subsection="guest-mode">Configure Guest Mode</button></section>`;
  }

  _overview(agent) {
    const usage = this._result?.usage || {};
    return `<section class="metric-grid" aria-label="Agent overview">
      ${this._metric("Provider & model", agent.provider, agent.model)}
      ${this._metric("Tokens today", usage.today?.total_tokens ?? 0)}
      ${this._metric("Tokens this month", usage.month?.total_tokens ?? 0)}
      ${this._metric("Lifetime tokens", usage.lifetime?.total_tokens ?? 0)}
      ${this._metric("Latest response", usage.latest?.total_tokens ?? "—", "tokens")}
      ${this._metric("Memories", this._titleCase(agent.memory_mode), `${formatUsageNumber(agent.memory_count)} memories`)}
      ${this._metric("Knowledge", agent.knowledge_enabled ? "Enabled" : "Disabled", `${formatUsageNumber(agent.knowledge_source_count)} sources`)}
      ${this._metric("Conversation archive", agent.archive_enabled ? "Enabled" : "Disabled")}
      ${this._metric("Guest Mode", this._titleCase(String(agent.guest_mode?.state || "inactive").replaceAll("_", " ")))}
    </section>`;
  }

  _usageBar(day, max) {
    const { total, cached, uncached } = tokenBreakdown(day.total_tokens,day.cached_input_tokens);
    const height = Math.max(2, total / max * 100);
    const cachedShare = total ? cached / total * 100 : 0;
    const uncachedShare = total ? uncached / total * 100 : 0;
    const details = `${day.date} · ${formatUsageNumber(total)} total · ${formatUsageNumber(cached)} cached input · ${formatUsageNumber(uncached)} uncached`;
    return `<span class="chart-column" tabindex="0" aria-label="${this._e(details)}" data-tooltip="${this._e(details)}" style="height:${height}%"><span class="chart-segment cached" style="height:${cachedShare}%"></span><span class="chart-segment uncached" style="height:${uncachedShare}%"></span></span>`;
  }

  _usage() {
    const result = this._result || {};
    const days = result.days?.days || [];
    const chartDays = days.slice(-31);
    const max = Math.max(1, ...days.map((day) => day.total_tokens));
    const today = result.summary?.today || {};
    const month = result.summary?.month || {};
    const lifetime = result.summary?.lifetime || {};
    const latest = result.summary?.latest || null;
    const cachedMeta = (value) => `${formatUsageNumber(value || 0)} cached input`;
    const loadWarnings = (result.load_errors || []).map((issue) => `<div class="notice"><strong>${this._e(issue.label)} unavailable</strong><p>${this._e(issue.message)} Other usage information is still shown where available.</p></div>`).join("");
    const dayLabel = (day) => {
      const value = String(day?.date || "");
      const date = new Date(`${value}T12:00:00`);
      if (!value || Number.isNaN(date.getTime())) return value;
      try { return new Intl.DateTimeFormat(undefined, {month:"short", day:"numeric", timeZone:this._hass?.config?.time_zone}).format(date); }
      catch (_) { return value; }
    };
    const chartAxis = chartDays.length ? `<div class="chart-axis" aria-hidden="true"><span>${this._e(dayLabel(chartDays[0]))}</span><span>${this._e(dayLabel(chartDays[Math.floor((chartDays.length - 1) / 2)]))}</span><span>${this._e(dayLabel(chartDays[chartDays.length - 1]))}</span></div>` : "";
    const recentRows = (result.runs?.runs || []).map((run) => {
      const tokens = tokenBreakdown(run.total_tokens,run.cached_input_tokens);
      const completed = formatUsageTimestamp(run.completed_at, undefined, this._hass?.config?.time_zone);
      return `<tr><td><time datetime="${this._e(completed.datetime)}" title="${this._e(completed.datetime)}">${this._e(completed.display)}</time></td><td>${formatUsageNumber(tokens.total)}</td><td>${formatUsageNumber(tokens.cached)}</td><td>${formatUsageNumber(tokens.uncached)}</td><td>${formatUsageNumber(run.request_count)}</td><td>${this._e(`${formatUsageNumber(run.duration_ms)} ms`)}</td><td>${this._e(run.successful ? "Success" : run.error_type || "Failed")}</td></tr>`;
    }).join("");
    return `${loadWarnings}<section class="metric-grid compact">${this._metric("Today",today.total_tokens || 0,cachedMeta(today.cached_input_tokens))}${this._metric("This month",month.total_tokens || 0,cachedMeta(month.cached_input_tokens))}${this._metric("Lifetime",lifetime.total_tokens || 0,cachedMeta(lifetime.cached_input_tokens))}${this._metric("Latest response",latest?.total_tokens ?? "—",latest ? cachedMeta(latest.cached_input_tokens) : "")}</section>
      <section class="content-card"><div class="chart-heading"><h2>Tokens by day</h2><div class="chart-legend" aria-label="Token categories"><span><i class="legend-swatch uncached"></i>Uncached</span><span><i class="legend-swatch cached"></i>Cached input</span></div></div><div class="chart" aria-label="Daily token usage; cached input tokens are included within each day's total">${chartDays.map((day) => this._usageBar(day,max)).join("") || this._empty("No completed runs yet.")}</div>${chartAxis}<p class="chart-note"><strong>Cached input</strong> is request content the provider has seen before and can reuse. It is included in the total token count, but cached input is usually cheaper than uncached input when the provider supports discounted caching.</p></section>
      <section class="content-card"><h2>Recent runs</h2><div class="table"><table><thead><tr>${["Completed", "Total", "Cached input", "Uncached", "Requests", "Duration", "Result"].map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${recentRows}</tbody></table></div></section>
      ${this._data?.is_admin ? `<section class="content-card"><h2>Usage detail maintenance</h2><p>Retention is available in the local Retention & maintenance subsection.</p><div class="section-actions"><button type="button" class="secondary inline-route" data-page="usage-maintenance" data-subsection="retention">Configure retention</button><button type="button" id="clear-details" class="danger secondary-danger">Clear recent details</button></div><small>Daily, monthly, and lifetime totals are never removed by detail pruning.</small></section>` : ""}`;
  }

  _conversations() {
    const result = this._contentData || this._result || {};
    const settings = result.settings || {};
    const active = result.active?.active || [];
    return `${this._data?.is_admin && active.length ? `<section class="content-card"><div class="section-heading"><div><h2>Active conversations</h2><p>Recent conversations that can continue when the same user or voice device speaks again.</p></div></div><div class="list">${active.map((item) => `<article class="list-card"><div class="card-main"><h3>${this._e(item.label)}</h3><p class="meta">Last active ${this._e(this._formatDate(item.last_active))} · Expires ${this._e(this._formatDate(item.expires_at))}</p></div><div class="actions"><button type="button" class="danger end-active" data-key="${this._e(item.key)}">Start fresh next time</button></div></article>`).join("")}</div></section>` : ""}<section class="notice ${settings.archive_enabled ? "on" : ""}"><div><strong>Conversation archive ${settings.archive_enabled ? "enabled" : "disabled"}</strong><p>Saved conversations are kept for ${settings.archive_retention_days || 30} days · Assistant search is ${settings.archive_model_search_enabled ? "on" : "off"}</p></div></section>
      <section class="content-card"><div class="section-heading"><div><h2>Retained conversations</h2><p>Search and review conversations for the selected scope.</p></div></div><div class="search-row"><input id="archive-query" type="search" placeholder="Search retained discussions" aria-label="Search retained discussions"><button type="button" id="archive-search">Search</button></div><div class="list">${(result.sessions?.sessions || []).map((item) => `<article class="list-card"><div class="card-main clickable open-session" tabindex="0" role="button" data-id="${this._e(item.session_id)}"><h3>${this._e(item.title || "Untitled conversation")}</h3><p class="meta">${this._e(this._formatDate(item.last_message_at))} · ${this._e(String(item.turn_count))} turns · ${this._e(item.scope_source)}</p></div><div class="actions"><button type="button" class="secondary view-session" data-id="${this._e(item.session_id)}">View</button><button type="button" class="danger delete-session" data-id="${this._e(item.session_id)}">Delete</button></div></article>`).join("") || this._empty("No retained conversations in this scope.")}</div></section>
      ${this._data?.is_admin ? `<p class="help">Continuity is recent context used for follow-ups. The archive is retained history; configure its behavior below.</p>` : ""}`;
  }

  _memories() {
    const items = this._filtered(this._result?.memories || [], (item) => `${item.content} ${item.category} ${item.source}`);
    const temporary = this._memoryKind === "temporary";
    return `<section class="content-card"><div class="section-heading"><div><h2>Memories</h2><p>${temporary ? "Short-term details that are removed automatically at their expiry time." : "Long-term facts the assistant can reuse in future conversations."}</p></div>${temporary ? "" : `<button type="button" id="add-memory">+ Add memory</button>`}</div><div class="config-jumps"><button type="button" class="secondary memory-kind" data-kind="persistent" ${temporary ? "" : "disabled"}>Long-term</button><button type="button" class="secondary memory-kind" data-kind="temporary" ${temporary ? "disabled" : ""}>Short-term</button></div><input id="list-search" class="search" type="search" value="${this._e(this._query)}" placeholder="Search memories" aria-label="Search memories"><div class="list memory-list">${items.map((memory) => temporary ? `<article class="list-card"><div class="card-main"><p class="primary-copy">${this._e(memory.content)}</p><p class="meta">${this._e(memory.category)} · Used for ${this._e(this._temporaryOwner(memory.scope_id))} · Expires ${this._e(this._formatDate(memory.expires_at))}</p></div><div class="actions"><button type="button" class="danger delete-temporary" data-id="${this._e(memory.memory_id)}" data-scope="${this._e(memory.scope_id || this._scopeId)}">Delete</button></div></article>` : `<article class="list-card"><div class="card-main clickable edit-memory" tabindex="0" role="button" data-id="${this._e(memory.memory_id)}"><p class="primary-copy">${this._e(memory.content)}</p><p class="meta">${this._e(memory.category)} · ${this._e(memory.source)} · Updated ${this._e(this._formatDate(memory.updated_at))}</p></div><div class="actions"><button type="button" class="secondary memory-edit-button" data-id="${this._e(memory.memory_id)}">Edit</button>${this._data?.is_admin && this._scopeId === "__anonymous__" ? `<button type="button" class="secondary reassign-memory" data-id="${this._e(memory.memory_id)}">Assign to user</button>` : ""}<button type="button" class="danger delete-memory" data-id="${this._e(memory.memory_id)}">Delete</button></div></article>`).join("") || this._empty(this._query ? "No memories match this filter." : `No ${temporary ? "short-term" : "long-term"} memories yet.`)}</div></section>`;
  }

  _knowledge() {
    const sources = this._result?.sources || [];
    const items = this._filtered(sources, (source) => `${source.title} ${source.description}`);
    return `<section class="content-card"><div class="section-heading"><div><h2>Knowledge Library</h2><p>${formatUsageNumber(sources.length)} source${sources.length === 1 ? "" : "s"} stored locally for on-demand search.</p></div><button type="button" id="add-source">+ Add source</button></div><input id="list-search" class="search" type="search" value="${this._e(this._query)}" placeholder="Filter by title or description" aria-label="Filter Knowledge sources"><div class="list knowledge-list">${items.map((source) => `<article class="list-card"><div class="card-main clickable edit-source" tabindex="0" role="button" data-id="${this._e(source.source_id)}"><h3>${this._e(source.title)}</h3><p class="description">${this._e(source.description || "No description")}</p><p class="meta">${formatUsageNumber(source.character_count || 0)} characters · Updated ${this._e(this._formatDate(source.updated_at))}</p></div><div class="actions"><button type="button" class="secondary source-edit-button" data-id="${this._e(source.source_id)}">Edit</button><button type="button" class="danger delete-source" data-id="${this._e(source.source_id)}">Delete</button></div></article>`).join("") || this._empty(this._query ? "No sources match this filter." : "No Knowledge sources yet. Add one to make reference information available on demand.")}</div></section>`;
  }

  _guestMode() {
    const status = this._result?.status || {};
    const policy = this._result?.policy || {};
    const state = String(status.state || "inactive").replaceAll("_", " ");
    return `<section class="page-intro guest-intro"><h1>Guest Mode</h1><p>Limit what visitors can see and do when they use this assistant. Restrictions are enforced by the integration, not only by model instructions.</p><button type="button" class="guide-topic-link guide-link" data-guide-topic="guest-mode">Learn about Guest Mode</button></section>${this._guestPolicyView(status, policy, state)}`;
  }

  _guestPolicyView(status, policy, state) {
    const config = this._guestDraft || this._result?.config || {};
    const selector = (key, type, label) => `<label class="setting"><span>${label}</span><ha-selector data-guest-key="${key}" data-guest-selector="${type}"></ha-selector></label>`;
    const manager = (key, type, label, description) => `<details class="guest-manager"><summary><span><strong>${label}</strong><small>${formatUsageNumber((config[key] || []).length)} excluded · ${description}</small></span><span class="manage-label">Manage</span></summary><div class="guest-manager-body">${selector(key,type,`Choose ${label.toLowerCase()}`)}</div></details>`;
    const mode = (key, label, values) => `<label>${label}<select data-guest-mode="${key}">${values.map(([value,text]) => `<option value="${value}" ${config[key] === value ? "selected" : ""}>${text}</option>`).join("")}</select></label>`;
    const intervalSummary = status.active_from ? `Starts ${this._e(this._formatDate(status.active_from))}${status.active_until ? ` · Ends ${this._e(this._formatDate(status.active_until))}` : " · No expiry"}` : "No interval configured";
    const hasInterval = Boolean(status.currently_active || status.scheduled || status.active_from || status.active_until);
    const activation = `<section class="content-card"><div class="section-heading"><div><h2>Guest Mode activation</h2><p class="meta">${this._e(this._titleCase(state))} · ${intervalSummary}</p><p>The assistant can enable or extend Guest Mode, but cannot shorten or disable it. Administrators and Home Assistant automations can change or end Guest Mode.</p></div></div>${this._data?.is_admin ? `<div class="form-grid"><label>Starts<input id="guest-start" type="datetime-local" value="${this._e(this._dateTimeLocal(status.active_from))}"></label><label>Ends<input id="guest-end" type="datetime-local" value="${this._e(this._dateTimeLocal(status.active_until))}" ${status.indefinite ? "disabled" : ""}></label></div><label class="toggle"><span>Remain active indefinitely</span><input id="guest-indefinite" type="checkbox" ${status.indefinite ? "checked" : ""}></label><div class="section-actions">${hasInterval ? `<button type="button" id="guest-update">Update interval</button><button type="button" class="secondary" id="guest-now">Activate now</button>` : `<button type="button" id="guest-now">Activate now</button><button type="button" class="secondary" id="guest-update">Update interval</button>`}${hasInterval ? `<button type="button" class="danger secondary-danger" id="guest-disable">${status.scheduled ? "Cancel schedule" : "End Guest Mode"}</button>` : ""}</div>` : ""}</section>`;
    const baseSelectors = `${manager("guest_excluded_labels","label","Labels","The easiest way to restrict groups of related entities")}${manager("guest_excluded_areas","area","Areas","Hide every exposed entity in selected areas")}${manager("guest_excluded_domains","domain","Domains","Hide entire entity types, such as cameras or device trackers")}${manager("guest_excluded_entities","entity","Individual entities","Hide specific entities not covered by the rules above")}`;
    const controlSelectors = `${manager("guest_control_excluded_labels","label","Labels","Make labelled entities read-only")}${manager("guest_control_excluded_areas","area","Areas","Make entities in selected areas read-only")}${manager("guest_control_excluded_domains","domain","Domains","Make entire domains read-only")}${manager("guest_control_excluded_entities","entity","Individual entities","Make specific entities read-only")}`;
    const futureKnowledge = "";
    const futureFunctions = "";
    const legacyNotice = this._result?.legacy_policy && !this._guestMigrationReview ? `<section class="notice legacy-migration"><strong>Previous Guest Mode settings found</strong><p>Your old Guest Mode rules used an allow-list. We have kept an equivalent restrictive draft so the upgrade does not accidentally give guests more access. The old policy remains enforced until you save.</p><div class="section-actions"><button type="button" id="guest-review-converted">Review converted settings</button><button type="button" class="secondary" id="guest-start-fresh">Start fresh with new defaults</button></div></section>` : "";
    const editor = this._data?.is_admin && (!this._result?.legacy_policy || this._guestMigrationReview) ? `<section class="content-card"><div class="section-heading"><div><h2>Home Assistant exclusions</h2><p>Guests can use entities normally available to this assistant, except those excluded here.</p></div></div><p class="help"><strong>Tip:</strong> Labels are usually the easiest way to manage Guest access. For example, create a <code>Guest restricted</code> label in Home Assistant and apply it to anything private or sensitive.</p><div class="guest-managers">${baseSelectors}</div><details class="guest-advanced"><summary>Advanced</summary><label class="toggle"><span>Make some visible entities read-only</span><input id="guest-separate-control" type="checkbox" ${config.guest_separate_control_restrictions ? "checked" : ""}></label><p class="help">Normally, anything a guest can see can also be controlled if the assistant is allowed to control it. Turn this on if you want guests to see some entities but not change them.</p>${config.guest_separate_control_restrictions ? `<div class="guest-managers">${controlSelectors}</div>` : ""}</details></section><section class="content-card"><div class="section-heading"><div><h2>Knowledge, functions & memory</h2><p>Personal memory and owner conversation archives are always unavailable.</p></div></div><div class="form-grid">${mode("guest_knowledge_policy","Allow Knowledge Library reads",[["off","Off"],["on","On"],["custom","Custom"]])}${mode("guest_function_policy","Allow custom functions",[["off","Off"],["on","On"],["custom","Custom"]])}${mode("guest_shared_memory_policy","Shared household memory",[["off","Off"],["read_only","Read only"],["read_write","Read & write"]])}</div>${futureKnowledge}${futureFunctions}${config.guest_knowledge_policy === "on" ? `<p class="help">All current Knowledge sources are available to guests. New sources added later will also be included. Choose Custom if you want a fixed list.</p>` : ""}${config.guest_function_policy === "on" ? `<p class="help">All eligible enabled functions are available to guests. New eligible functions added later will also be included. Choose Custom if you want a fixed list.</p>` : ""}${config.guest_knowledge_policy === "custom" ? selector("guest_knowledge_source_ids","knowledge","Allowed Knowledge sources") : ""}${config.guest_function_policy === "custom" ? `${selector("guest_allowed_function_names","function","Allowed functions")}${selector("guest_allowed_group_ids","group","Allowed Function Groups")}` : ""}</section><section class="content-card"><div class="section-heading"><div><h2>Assistant permission</h2><p>This permission is separate from the Guest Mode activation state.</p></div></div><details class="guest-advanced"><summary>Advanced</summary><label class="toggle"><span>Allow the assistant to activate Guest Mode</span><input id="guest-controls-enabled" type="checkbox" ${config.guest_mode_enabled ? "checked" : ""}></label><p class="help">Lets the assistant activate Guest Mode, start it earlier, or extend it. The assistant can never shorten or disable Guest Mode. Turning this off never ends or weakens an active restriction.</p></details><div class="section-actions"><button type="button" id="guest-policy-save">Save Guest policy</button></div></section>` : "";
    return `${activation}<section class="metric-grid compact">${this._metric("Guest-visible entities", policy.readable_entity_count ?? "—")}${this._metric("Guest-controllable entities", policy.controllable_entity_count ?? "—")}${this._metric("Guest functions", policy.configured_tool_count ?? "—")}${this._metric("Guest archive retention", "Disabled")}</section>${legacyNotice}${editor}<section class="notice"><strong>Voice and model safety</strong><p>Model-visible context is fully rebuilt on the next user turn; execution restrictions tighten immediately, but context already sent to a provider cannot be removed retroactively.</p></section><details class="content-card"><summary><strong>Capability safety</strong></summary><p>Guest permissions are enforced by Extended OpenAI. Custom, composite, or script capabilities are unavailable when their side effects cannot be safely limited to guest-approved entities. This is intentional.</p></details>`;
  }

  _setupGuestSelectors() {
    const config = this._guestDraft || {};
    const result = this._result || {};
    const select = (items, value, label) => ({select: {multiple: true, custom_value: false, options: items.map((item) => ({value: value(item), label: label(item)}))}});
    this.shadowRoot.querySelectorAll("ha-selector[data-guest-key]").forEach((element) => {
      const type = element.dataset.guestSelector;
      element.hass = this.hass;
      element.value = config[element.dataset.guestKey] || [];
      element.selector = type === "entity" ? {entity: {multiple: true}} : type === "area" ? {area: {multiple: true}} : type === "label" ? {label: {multiple: true}} : type === "domain" ? select(result.domains || [], (item) => item, (item) => item) : type === "knowledge" ? select(result.knowledge_sources || [], (item) => item.source_id, (item) => `${item.title} — ${item.description || "No description"}`) : type === "group" ? select(result.function_groups || [], (item) => item.id, (item) => `${item.name} — ${item.description}`) : select((result.functions || []).filter((item) => !item.unsafe_in_guest_mode), (item) => item.name, (item) => `${item.name}${item.enabled ? "" : " (disabled)"} — ${item.description || "No description"}`);
      element.addEventListener("value-changed", (event) => { config[element.dataset.guestKey] = event.detail.value || []; });
    });
  }

  async _saveGuestPolicy() {
    try {
      await this._call("guest_mode", "save_policy", {config: this._guestDraft});
      await this._loadSection(true);
      this._toast("Guest policy saved");
    } catch (err) { this._toast(`Unable to save Guest policy: ${err.message || String(err)}`, true); }
  }

  async _startFreshGuestPolicy() {
    if (!await this._confirm(
      "Start a fresh Guest policy?",
      "Starting fresh means guests will be able to use all Home Assistant entities normally available to this assistant unless you add exclusions. The existing policy remains enforced until you save.",
      "Start fresh",
    )) return;
    this._guestDraft = freshGuestPolicyDraft(this._guestDraft);
    this._guestMigrationReview = true;
    this._guestStartingFresh = true;
    this._render();
  }

  _dateTimeLocal(value) {
    if (!value) return "";
    const date = new Date(value);
    const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return shifted.toISOString().slice(0, 16);
  }

  async _updateGuestMode(now = false) {
    const root = this.shadowRoot;
    const indefinite = root.querySelector("#guest-indefinite")?.checked ?? true;
    const start = now ? new Date().toISOString() : root.querySelector("#guest-start")?.value;
    const end = root.querySelector("#guest-end")?.value;
    try {
      await this._call("guest_mode", "update", {
        ...(start ? {active_from: start} : {}),
        ...(!indefinite && end ? {active_until: end} : {}),
        indefinite: indefinite || !end,
      });
      await this._loadAgents(this._agentId);
      this._toast("Guest Mode updated");
    } catch (err) { this._toast(`Unable to update Guest Mode: ${err.message || String(err)}`, true); }
  }

  async _disableGuestMode() {
    if (!await this._confirm("End Guest Mode?", "This immediately ends an active interval or cancels a future schedule.", "End Guest Mode")) return;
    try {
      await this._call("guest_mode", "disable");
      await this._loadAgents(this._agentId);
      this._toast("Guest Mode ended");
    } catch (err) { this._toast(`Unable to end Guest Mode: ${err.message || String(err)}`, true); }
  }

  _diagnostics(agent) {
    return `<section class="metric-grid">${this._metric("Agent", agent.title)}${this._metric("Provider", agent.provider)}${this._metric("Model", agent.model)}${this._metric("Conversation archive", agent.archive_enabled ? "Enabled" : "Disabled")}${this._metric("Guest Mode", this._titleCase(String(agent.guest_mode?.state || "inactive").replaceAll("_", " ")))}</section><section class="content-card"><h2>Test provider connection</h2><p>Sends one minimal request to check the selected provider and model. It does not run Home Assistant actions.</p><button type="button" id="test-agent">Run connection test</button><pre id="test-result" aria-live="polite"></pre><small>If the test fails, verify the provider credentials, API format, model name, and network access.</small></section>`;
  }

  _dialogs() {
    return `<dialog id="knowledge-dialog" class="editor-dialog wide" aria-labelledby="knowledge-dialog-title"><form id="knowledge-form"><div class="dialog-header"><h2 id="knowledge-dialog-title">Add Knowledge source</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Title<input id="knowledge-title" maxlength="${KNOWLEDGE_TITLE_LIMIT}" required></label><label>Description<textarea id="knowledge-description" class="short-textarea" maxlength="${KNOWLEDGE_DESCRIPTION_LIMIT}" spellcheck="true"></textarea></label><label>Content<textarea id="knowledge-content" class="knowledge-editor" maxlength="${KNOWLEDGE_LIMIT}" required spellcheck="true"></textarea></label><div id="knowledge-counter" class="counter">0 / ${KNOWLEDGE_LIMIT.toLocaleString()} characters</div><div id="knowledge-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="knowledge-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="knowledge-save">Save</button></div></form></dialog>
      <dialog id="memory-dialog" class="editor-dialog" aria-labelledby="memory-dialog-title"><form id="memory-form"><div class="dialog-header"><h2 id="memory-dialog-title">Add memory</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Memory<textarea id="memory-content" required spellcheck="true" placeholder="What should the agent remember?"></textarea></label><label>Category<input id="memory-category" value="general" required></label><p id="memory-meta" class="meta"></p><div id="memory-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="memory-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="memory-save">Save</button></div></form></dialog>
      <dialog id="session-dialog" class="editor-dialog wide" aria-labelledby="session-title"><div class="dialog-header"><h2 id="session-title">Conversation</h2><button type="button" class="icon close-session" aria-label="Close">×</button></div><div id="session-body" class="dialog-body session-body"></div><div class="dialog-actions"><button type="button" class="secondary close-session">Close</button></div></dialog>
      <dialog id="reassign-dialog" class="editor-dialog" aria-labelledby="reassign-title"><div class="dialog-header"><h2 id="reassign-title">Assign unowned memory</h2></div><div class="dialog-body"><p class="help">Choose the user or household that should be able to use this older memory.</p><label>Assign to<select id="reassign-scope">${this._scopeOptions("memories", true, true)}</select></label></div><div class="dialog-actions"><button type="button" class="secondary" id="reassign-cancel">Cancel</button><button type="button" id="reassign-save">Assign memory</button></div></dialog>
      <dialog id="confirm-dialog" class="editor-dialog confirm-dialog" aria-labelledby="confirm-title"><div class="dialog-header"><h2 id="confirm-title">Confirm</h2></div><div class="dialog-body"><p id="confirm-message"></p></div><div class="dialog-actions"><button type="button" class="secondary" id="confirm-cancel">Cancel</button><button type="button" class="danger" id="confirm-accept">Confirm</button></div></dialog>
      ${requestRulesDialog(this)}${configurationDialogs(this)}${restoreDialog(this)}`;
  }

  _bindActions() {
    const root = this.shadowRoot;
    const q = (selector) => root.querySelector(selector);
    q("#list-search")?.addEventListener("input", (event) => { this._query = event.target.value; this._updateVisibleList(); });
    q("#add-source")?.addEventListener("click", () => this._openKnowledge());
    q("#add-memory")?.addEventListener("click", () => this._openMemory());
    root.querySelectorAll(".edit-source, .source-edit-button").forEach((element) => this._activate(element, () => this._openKnowledge(element.dataset.id)));
    root.querySelectorAll(".edit-memory, .memory-edit-button").forEach((element) => this._activate(element, () => this._openMemory(element.dataset.id)));
    root.querySelectorAll(".open-session, .view-session").forEach((element) => this._activate(element, () => this._openSession(element.dataset.id)));
    root.querySelectorAll(".delete-source").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteSource(button.dataset.id); }));
    root.querySelectorAll(".delete-memory").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteMemory(button.dataset.id); }));
    root.querySelectorAll(".delete-temporary").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteTemporaryMemory(button.dataset.id, button.dataset.scope); }));
    root.querySelectorAll(".memory-kind").forEach((button) => button.addEventListener("click", async () => { this._memoryKind = button.dataset.kind; this._query = ""; await this._loadSection(); }));
    root.querySelectorAll(".end-active").forEach((button) => button.addEventListener("click", async () => { if (!await this._confirm("End active conversation?", "The next matching Assist request will start with fresh model context.", "End conversation")) return; await this._call("conversations", "end_active", { continuity_key: button.dataset.key }); await this._loadSection(); }));
    root.querySelectorAll(".delete-session").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteSession(button.dataset.id); }));
    root.querySelectorAll(".reassign-memory").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._openReassign(button.dataset.id); }));
    root.querySelectorAll(".close-editor").forEach((button) => button.addEventListener("click", () => this._requestEditorClose()));
    root.querySelectorAll(".close-session").forEach((button) => button.addEventListener("click", () => q("#session-dialog").close()));
    q("#knowledge-form")?.addEventListener("submit", (event) => { event.preventDefault(); this._saveKnowledge(); });
    q("#memory-form")?.addEventListener("submit", (event) => { event.preventDefault(); this._saveMemory(); });
    q("#knowledge-content")?.addEventListener("input", () => this._updateKnowledgeCounter());
    q("#knowledge-delete")?.addEventListener("click", () => this._deleteSource(this._editingSource?.source_id, true));
    q("#memory-delete")?.addEventListener("click", () => this._deleteMemory(this._editingMemory?.memory_id, true));
    [q("#knowledge-dialog"), q("#memory-dialog")].forEach((dialog) => dialog?.addEventListener("cancel", (event) => { event.preventDefault(); this._requestEditorClose(); }));
    q("#session-dialog")?.addEventListener("cancel", (event) => { event.preventDefault(); q("#session-dialog").close(); });
    q("#reassign-cancel")?.addEventListener("click", () => q("#reassign-dialog").close());
    q("#reassign-save")?.addEventListener("click", () => this._saveReassign());
    q("#clear-details")?.addEventListener("click", () => this._clearUsageDetails());
    q("#archive-search")?.addEventListener("click", () => this._searchArchive());
    q("#archive-query")?.addEventListener("keydown", (event) => { if (event.key === "Enter") this._searchArchive(); });
    q("#test-agent")?.addEventListener("click", () => this._testAgent());
    q("#guest-indefinite")?.addEventListener("change", (event) => { const end = q("#guest-end"); if (end) end.disabled = event.target.checked; });
    q("#guest-update")?.addEventListener("click", () => this._updateGuestMode(false));
    q("#guest-now")?.addEventListener("click", () => this._updateGuestMode(true));
    q("#guest-disable")?.addEventListener("click", () => this._disableGuestMode());
    q("#guest-policy-save")?.addEventListener("click", () => this._saveGuestPolicy());
    q("#guest-review-converted")?.addEventListener("click", () => { this._guestMigrationReview = true; this._render(); });
    q("#guest-start-fresh")?.addEventListener("click", () => this._startFreshGuestPolicy());
    q("#guest-separate-control")?.addEventListener("change", (event) => { this._guestDraft.guest_separate_control_restrictions = event.target.checked; this._render(); });
    q("#guest-controls-enabled")?.addEventListener("change", (event) => { this._guestDraft.guest_mode_enabled = event.target.checked; });
    root.querySelectorAll("[data-guest-mode]").forEach((element) => element.addEventListener("change", () => { this._guestDraft[element.dataset.guestMode] = element.value; this._render(); }));
    if (this._viewKey() === "capabilities/guest-mode") this._setupGuestSelectors();
    if (this._page === "assistant" || ["data-memory/conversations", "usage-maintenance/backup-restore", "usage-maintenance/retention"].includes(this._viewKey())) bindConfiguration(this);
    if (this._viewKey() === "capabilities/functions") bindTools(this);
    if (this._viewKey() === "capabilities/request-rules") bindRequestRules(this);
    if (this._viewKey() === "overview") bindOverview(this);
    if (this._viewKey() === "guide") bindGuide(this);
    if (this._pendingSettingFocus) {
      const target = this._pendingSettingFocus;
      this._pendingSettingFocus = null;
      requestAnimationFrame(() => { const element = this.shadowRoot.querySelector(`#${target}`); element?.scrollIntoView({behavior:"smooth", block:"start"}); (element?.querySelector("input,select,textarea,button") || element)?.focus?.(); });
    }
  }

  _activate(element, callback) {
    element.addEventListener("click", (event) => { if (!event.target.closest("button") || event.currentTarget === event.target) callback(); });
    element.addEventListener("keydown", (event) => { if ((event.key === "Enter" || event.key === " ") && event.target === element) { event.preventDefault(); callback(); } });
  }

  _updateVisibleList() {
    const query = this._query.trim().toLocaleLowerCase();
    this.shadowRoot.querySelectorAll(".list-card").forEach((card) => {
      card.hidden = query && !card.textContent.toLocaleLowerCase().includes(query);
    });
  }

  async _openKnowledge(sourceId = null) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#knowledge-dialog");
    const loadToken = (this._knowledgeLoadToken || 0) + 1;
    this._knowledgeLoadToken = loadToken;
    this._editingSource = null;
    this._knowledgeMode = sourceId ? "edit-loading" : "create";
    this._editorInitial = null;
    this._setDialogError("knowledge", "");
    root.querySelector("#knowledge-title").value = "";
    root.querySelector("#knowledge-description").value = "";
    root.querySelector("#knowledge-content").value = "";
    root.querySelector("#knowledge-delete").hidden = true;
    root.querySelector("#knowledge-dialog-title").textContent = sourceId ? "Loading source…" : "Add Knowledge source";
    this._setKnowledgeEditorDisabled(Boolean(sourceId));
    this._editorKind = "knowledge";
    dialog.showModal();
    if (sourceId) {
      try {
        const response = await this._call("knowledge", "get", { source_id: sourceId });
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._editingSource = response.source;
        this._knowledgeMode = "edit";
        root.querySelector("#knowledge-title").value = response.source.title || "";
        root.querySelector("#knowledge-description").value = response.source.description || "";
        root.querySelector("#knowledge-content").value = response.source.content || "";
        root.querySelector("#knowledge-delete").hidden = false;
        root.querySelector("#knowledge-dialog-title").textContent = "Edit Knowledge source";
        this._setKnowledgeEditorDisabled(false);
      } catch (err) {
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._knowledgeMode = "edit-error";
        this._setDialogError("knowledge", `Unable to load source: ${err.message || String(err)}`);
        root.querySelector("#knowledge-dialog-title").textContent = "Unable to load source";
      }
    }
    if (["create", "edit"].includes(this._knowledgeMode)) {
      this._editorInitial = this._knowledgeValues();
    }
    this._updateKnowledgeCounter();
    requestAnimationFrame(() => (this._knowledgeMode === "edit-error" ? root.querySelector(".close-editor") : root.querySelector("#knowledge-title")).focus());
  }

  async _openMemory(memoryId = null) {
    const root = this.shadowRoot;
    const memory = (this._result?.memories || []).find((item) => item.memory_id === memoryId) || null;
    this._editingMemory = memory;
    this._editorKind = "memory";
    root.querySelector("#memory-dialog-title").textContent = memory ? "Edit memory" : "Add memory";
    root.querySelector("#memory-content").value = memory?.content || "";
    root.querySelector("#memory-category").value = memory?.category || "general";
    root.querySelector("#memory-delete").hidden = !memory;
    root.querySelector("#memory-meta").textContent = memory ? [memory.source, memory.created_at ? `Created ${this._formatDate(memory.created_at)}` : "", memory.updated_at ? `Updated ${this._formatDate(memory.updated_at)}` : ""].filter(Boolean).join(" · ") : "Categories help organise memories.";
    this._setDialogError("memory", "");
    this._editorInitial = this._memoryValues();
    root.querySelector("#memory-dialog").showModal();
    requestAnimationFrame(() => root.querySelector("#memory-content").focus());
  }

  async _requestEditorClose() {
    const kind = this._editorKind;
    const dialog = this.shadowRoot.querySelector(`#${kind}-dialog`);
    if (!dialog?.open) return;
    const current = kind === "knowledge" ? this._knowledgeValues() : this._memoryValues();
    if (this._editorInitial !== null && JSON.stringify(current) !== JSON.stringify(this._editorInitial)) {
      const discard = await this._confirm("Discard unsaved changes?", "Your changes have not been saved.", "Discard");
      if (!discard) return;
    }
    if (kind === "knowledge") this._knowledgeLoadToken = (this._knowledgeLoadToken || 0) + 1;
    dialog.close();
  }

  _setKnowledgeEditorDisabled(disabled) {
    const root = this.shadowRoot;
    ["#knowledge-title", "#knowledge-description", "#knowledge-content", "#knowledge-save"].forEach((selector) => {
      root.querySelector(selector).disabled = disabled;
    });
  }

  _knowledgeValues() {
    const root = this.shadowRoot;
    return { title: root.querySelector("#knowledge-title").value, description: root.querySelector("#knowledge-description").value, content: root.querySelector("#knowledge-content").value };
  }

  _memoryValues() {
    const root = this.shadowRoot;
    return { content: root.querySelector("#memory-content").value, category: root.querySelector("#memory-category").value };
  }

  async _saveKnowledge() {
    const button = this.shadowRoot.querySelector("#knowledge-save");
    if (button.disabled || !["create", "edit"].includes(this._knowledgeMode)) return;
    const values = this._knowledgeValues();
    const editing = this._knowledgeMode === "edit";
    this._setSaving(button, true);
    try {
      await this._call("knowledge", editing ? "update" : "create", { ...(editing ? { source_id: this._editingSource.source_id } : {}), ...values });
      this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast(editing ? "Knowledge source updated" : "Knowledge source saved");
    } catch (err) {
      this._setDialogError("knowledge", `Unable to save source: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _saveMemory() {
    const button = this.shadowRoot.querySelector("#memory-save");
    if (button.disabled) return;
    const values = this._memoryValues();
    this._setSaving(button, true);
    try {
      await this._call("memories", this._editingMemory ? "update" : "add", { scope_id: this._scopeId, ...(this._editingMemory ? { memory_id: this._editingMemory.memory_id } : {}), content: values.content.trim(), category: values.category.trim() || "general" });
      this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast(this._editingMemory ? "Memory updated" : "Memory added");
    } catch (err) {
      this._setDialogError("memory", `Unable to save memory: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _deleteSource(sourceId, fromDialog = false) {
    if (!sourceId || !await this._confirm("Delete Knowledge source?", "This permanently removes the selected source from this agent's local Knowledge Library.", "Delete")) return;
    try {
      await this._call("knowledge", "delete", { source_id: sourceId, confirm: true });
      if (fromDialog) this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Knowledge source deleted");
    } catch (err) { this._toast(`Unable to delete source: ${err.message || String(err)}`, true); }
  }

  async _deleteMemory(memoryId, fromDialog = false) {
    if (!memoryId || !await this._confirm("Delete memory?", "This memory will be permanently removed from the selected scope.", "Delete")) return;
    try {
      await this._call("memories", "delete", { scope_id: this._scopeId, memory_id: memoryId });
      if (fromDialog) this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Memory deleted");
    } catch (err) { this._toast(`Unable to delete memory: ${err.message || String(err)}`, true); }
  }

  async _deleteTemporaryMemory(memoryId, scopeId) {
    if (!memoryId || !await this._confirm("Delete temporary memory?", "This short-lived fact will no longer be included in later requests.", "Delete")) return;
    try {
      await this._call("memories", "temporary_delete", { scope_id: this._scopeId, temporary_scope_id: scopeId, memory_id: memoryId });
      await this._refreshAfterMutation();
      this._toast("Temporary memory deleted");
    } catch (err) { this._toast(`Unable to delete temporary memory: ${err.message || String(err)}`, true); }
  }

  async _openSession(sessionId) {
    const root = this.shadowRoot;
    const dialog = root.querySelector("#session-dialog");
    root.querySelector("#session-title").textContent = "Loading conversation…";
    root.querySelector("#session-body").innerHTML = this._loading();
    dialog.showModal();
    try {
      const data = await this._call("conversations", "get", { scope_id: this._scopeId, session_id: sessionId, limit: 100 });
      root.querySelector("#session-title").textContent = data.session.title || "Untitled conversation";
      root.querySelector("#session-body").innerHTML = data.turns.map((turn) => `<article class="turn"><div class="message user"><strong>You</strong><p>${this._e(turn.user_text)}</p></div><div class="message assistant"><strong>Assistant</strong><p>${this._e(turn.assistant_text)}</p></div><small>${this._e(this._formatDate(turn.timestamp))}</small></article>`).join("") || this._empty("No turns retained.");
    } catch (err) { root.querySelector("#session-body").innerHTML = `<div class="error" role="alert">${this._e(err.message || String(err))}</div>`; }
  }

  async _deleteSession(sessionId) {
    if (!await this._confirm("Delete conversation?", "This retained conversation and its turns will be permanently removed.", "Delete")) return;
    try { await this._call("conversations", "delete", { scope_id: this._scopeId, session_id: sessionId }); await this._refreshAfterMutation(); this._toast("Conversation deleted"); }
    catch (err) { this._toast(`Unable to delete conversation: ${err.message || String(err)}`, true); }
  }

  _openReassign(memoryId) {
    this._reassignMemoryId = memoryId;
    this.shadowRoot.querySelector("#reassign-dialog").showModal();
  }

  async _saveReassign() {
    const target = this.shadowRoot.querySelector("#reassign-scope").value;
    if (!target) return;
    try {
      const result = await this._call("memories", "reassign_legacy", { scope_id: "__anonymous__", target_scope_id: target, memory_ids: [this._reassignMemoryId] });
      this.shadowRoot.querySelector("#reassign-dialog").close();
      await this._refreshAfterMutation();
      this._toast(`Reassigned ${formatUsageNumber(result.reassigned)} memory record${result.reassigned === 1 ? "" : "s"}`);
    } catch (err) { this._toast(`Unable to reassign memory: ${err.message || String(err)}`, true); }
  }

  async _clearUsageDetails() {
    if (!await this._confirm("Clear recent usage details?", "Request and run details will be removed. Daily, monthly, and lifetime totals remain.", "Clear details")) return;
    try { await this._call("usage", "clear_details", { confirm: true }); await this._loadSection(); this._toast("Recent usage details cleared"); }
    catch (err) { this._toast(`Unable to clear details: ${err.message || String(err)}`, true); }
  }

  async _searchArchive() {
    const input = this.shadowRoot.querySelector("#archive-query");
    const button = this.shadowRoot.querySelector("#archive-search");
    if (button.disabled) return;
    this._setSaving(button, true, "Searching…");
    try {
      const found = await this._call("conversations", "search", { scope_id: this._scopeId, query: input.value, limit: 20 });
      const target = this._contentData || this._result;
      target.sessions = { sessions: found.results.map((item) => ({ ...item, last_message_at: item.timestamp, turn_count: "matching" })) };
      this._render();
    } catch (err) { this._toast(`Unable to search: ${err.message || String(err)}`, true); }
    finally { this._setSaving(button, false); }
  }

  async _testAgent() {
    const output = this.shadowRoot.querySelector("#test-result");
    output.textContent = "Testing…";
    try { output.textContent = JSON.stringify(await this._call("diagnostics", "test_agent"), null, 2); }
    catch (err) { output.textContent = err.message || String(err); }
  }

  async _refreshAfterMutation() {
    await this._loadSection(true);
  }

  _confirm(title, message, confirmLabel = "Confirm") {
    const root = this.shadowRoot;
    root.querySelector("#confirm-title").textContent = title;
    root.querySelector("#confirm-message").textContent = message;
    root.querySelector("#confirm-accept").textContent = confirmLabel;
    root.querySelector("#confirm-dialog").showModal();
    return new Promise((resolve) => { this._confirmResolver = resolve; });
  }

  _resolveConfirm(value) {
    const dialog = this.shadowRoot.querySelector("#confirm-dialog");
    if (dialog?.open) dialog.close();
    const resolver = this._confirmResolver;
    this._confirmResolver = null;
    if (resolver) resolver(value);
  }

  _setSaving(button, saving, label = "Saving…") {
    if (!button) return;
    if (saving) button.dataset.label = button.textContent;
    button.disabled = saving;
    button.textContent = saving ? label : button.dataset.label || "Save";
  }

  _setDialogError(kind, message) {
    const element = this.shadowRoot.querySelector(`#${kind}-error`);
    if (element) element.textContent = message;
  }

  _updateKnowledgeCounter() {
    const length = this.shadowRoot.querySelector("#knowledge-content")?.value.length || 0;
    this.shadowRoot.querySelector("#knowledge-counter").textContent = `${formatUsageNumber(length)} / ${formatUsageNumber(KNOWLEDGE_LIMIT)} characters`;
  }

  _toast(message, error = false) {
    const toast = this.shadowRoot.querySelector("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast visible${error ? " toast-error" : ""}`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { toast.className = "toast"; }, 5000);
  }

  _scopePicker() {
    const memories = this._viewKey() === "data-memory/memories";
    const hasEmpty = (this._data?.scopes || []).some((scope) => (memories ? scope.memory_count : scope.conversation_count) === 0 && scope.scope_type === "user" && !scope.is_current_user);
    return `<section class="scope-bar"><label><span>${memories ? "Show memories available to" : "Show conversations belonging to"}</span><select id="scope">${this._scopeOptions(memories ? "memories" : "conversations")}</select></label>${hasEmpty ? `<label class="show-empty"><input id="show-empty-scopes" type="checkbox" ${this._showEmptyScopes ? "checked" : ""}> Show users with no ${memories ? "memories" : "conversations"}</label>` : ""}${this._data?.is_admin ? `<small>You can view data for all users because you are an administrator.</small>` : ""}</section>`;
  }

  _scopeOptions(section, includeEmpty = this._showEmptyScopes, excludeLegacy = false) {
    const key = section === "memories" ? "memory_count" : "conversation_count";
    const scopes = [...(this._data?.scopes || [])].filter((scope) => !excludeLegacy || scope.scope_type !== "anonymous_legacy");
    scopes.sort((a, b) => {
      if (a.scope_type === "anonymous_legacy") return 1;
      if (b.scope_type === "anonymous_legacy") return -1;
      if (a.is_current_user !== b.is_current_user) return a.is_current_user ? -1 : 1;
      const populated = Number(b[key] > 0) - Number(a[key] > 0);
      return populated || a.display_name.localeCompare(b.display_name);
    });
    const visible = scopes.filter((scope) => scope.scope_id === this._scopeId || scope.is_current_user || scope[key] > 0 || scope.scope_type !== "user" || includeEmpty);
    return visible.map((scope) => `<option value="${this._e(scope.scope_id)}" ${scope.scope_id === this._scopeId ? "selected" : ""}>${this._e(scope.display_name)} (${formatUsageNumber(scope[key] || 0)})${scope.is_current_user ? " · You" : ""}</option>`).join("");
  }

  _filtered(items, value) {
    const query = this._query.trim().toLocaleLowerCase();
    return query ? items.filter((item) => value(item).toLocaleLowerCase().includes(query)) : items;
  }

  _retentionOptions(selected) { return [0,7,30,90,180,365].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value ? `${formatUsageNumber(value)} days` : "Disabled"}</option>`).join(""); }
  _metric(title, value, detail = "") { const display = typeof value === "number" && Number.isFinite(value) ? formatUsageNumber(value) : String(value); return `<article class="metric"><span>${this._e(title)}</span><strong>${this._e(display)}</strong>${detail ? `<small>${this._e(String(detail))}</small>` : ""}</article>`; }
  _toggle(id, label, checked) { return `<label class="toggle"><span>${this._e(label)}</span><input id="${id}" type="checkbox" role="switch" ${checked ? "checked" : ""}></label>`; }
  _table(headers, rows) { return `<div class="table"><table><thead><tr>${headers.map((header) => `<th>${this._e(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${this._e(typeof value === "number" && Number.isFinite(value) ? formatUsageNumber(value) : String(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }
  _loading() { return `<div class="loading" role="status"><span class="spinner"></span>Loading…</div>`; }
  _empty(message) { return `<div class="empty">${this._e(message)}</div>`; }
  _label(value) { return value[0].toUpperCase() + value.slice(1); }
  _titleCase(value) { return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
  _temporaryOwner(scopeId) {
    const known = (this._data?.scopes || []).find((scope) => scope.scope_id === scopeId);
    if (known) return known.display_name;
    if (String(scopeId || "").startsWith("device:")) return "Assist device";
    if (String(scopeId || "").startsWith("conversation:")) return "Current Assist conversation";
    return "Current user";
  }

  _formatDate(value) {
    if (!value) return "Unknown date";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try { return date.toLocaleString(undefined, { timeZone: this._hass?.config?.time_zone }); }
    catch (_) { return date.toLocaleString(); }
  }
  _e(value) { return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]); }

  _styles() { return `
    .guest-selector-grid{margin-top:18px}.guest-selector-grid ha-selector,.content-card>label>ha-selector,.guest-manager ha-selector{display:block;width:100%;min-width:0}.guest-managers{display:grid;border:1px solid var(--divider-color);border-radius:11px;overflow:hidden}.guest-manager{margin:0;padding:0 16px;border:0;border-bottom:1px solid var(--divider-color)}.guest-manager:last-child{border-bottom:0}.guest-manager summary{display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:66px;list-style:none}.guest-manager summary::-webkit-details-marker{display:none}.guest-manager summary>span:first-child{display:grid;gap:4px}.guest-manager small{font-weight:400}.manage-label{color:var(--primary-color)}.guest-manager-body{padding:0 0 16px}.guest-advanced{margin-top:24px}.legacy-migration .section-actions{margin-top:16px}
    [hidden]{display:none!important}
    :host{display:block;min-height:100%;padding:28px;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui);box-sizing:border-box;background:var(--primary-background-color)}*{box-sizing:border-box}.page-shell{max-width:1220px;margin:auto}header{display:flex;justify-content:space-between;gap:36px;align-items:end;margin-bottom:28px}.page-heading h1{margin:0;font-size:30px;font-weight:500}.page-heading p,.section-heading p,.notice p{margin:6px 0 0;color:var(--secondary-text-color);line-height:1.5}.agent-picker{width:min(390px,100%)}label{display:grid;gap:7px;font-size:13px;color:var(--secondary-text-color)}input,select,textarea,button{font:inherit}input,select,textarea{width:100%;min-height:42px;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:9px;padding:10px 12px}textarea{resize:vertical;line-height:1.55}button{min-height:42px;border:0;border-radius:9px;padding:9px 16px;cursor:pointer;background:var(--primary-color);color:var(--text-primary-color)}button.secondary{background:transparent;color:var(--primary-color);border:1px solid var(--primary-color)}button.danger{background:var(--error-color,#db4437);color:#fff}.secondary-danger{margin-left:auto}button.icon{min-width:42px;padding:4px;background:transparent;color:var(--secondary-text-color);font-size:25px}button:disabled{opacity:.6;cursor:wait}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,[tabindex]:focus-visible,summary:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}nav{display:flex;overflow:auto;border-bottom:1px solid var(--divider-color);margin-bottom:28px}nav button{background:transparent;color:var(--secondary-text-color);border-radius:0;padding:13px 18px;white-space:nowrap}nav button.active{color:var(--primary-color);border-bottom:3px solid var(--primary-color)}main{display:grid;gap:30px}.scope-bar,.content-card,.metric,.notice{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:13px}.scope-bar{max-width:1220px;margin:0 auto 28px;padding:18px 22px;display:flex;align-items:end;gap:20px;flex-wrap:wrap}.scope-bar>label:first-child{min-width:min(420px,100%)}.scope-bar .show-empty{display:flex;grid-gap:8px;align-items:center;min-height:42px}.show-empty input{width:18px;min-height:18px}.scope-bar small{margin-left:auto}.content-card{padding:24px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:18px}.metric-grid.compact{grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}.metric{padding:20px;display:grid;gap:7px}.metric span,.meta,small,.help,.counter{color:var(--secondary-text-color)}.metric strong{font-size:22px;font-weight:500}.section-heading{display:flex;align-items:start;justify-content:space-between;gap:24px;margin-bottom:20px}.section-heading h2,.content-card>h2{margin:0;font-size:20px}.search,.search-row{margin-bottom:20px}.search-row{display:flex;gap:12px}.search-row input{flex:1}.list{display:grid;gap:12px}.list-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:16px;align-items:center;border:1px solid var(--divider-color);border-radius:11px;padding:17px}.card-main.clickable{cursor:pointer;border-radius:8px;padding:4px;margin:-4px}.card-main.clickable:hover{background:var(--secondary-background-color)}.list-card h3,.primary-copy{margin:0;font-size:16px;line-height:1.45;overflow-wrap:anywhere}.list-card .description{margin:5px 0;line-height:1.45;overflow-wrap:anywhere}.meta{margin:6px 0 0;font-size:12px;line-height:1.45}.actions,.section-actions,.dialog-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.actions button{min-height:38px;padding:7px 12px}.notice{padding:20px 22px;border-left:4px solid var(--warning-color,#f9ab00)}.notice.on{border-left-color:var(--success-color,#0f9d58)}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 22px}fieldset{border:0;border-top:1px solid var(--divider-color);padding:26px 0 4px;margin:24px 0 0}legend{padding-right:14px;font-size:16px;font-weight:600}.toggle{display:flex;align-items:center;justify-content:space-between;min-height:42px}.toggle input{width:42px;height:24px;min-height:24px;accent-color:var(--primary-color)}details{border-top:1px solid var(--divider-color);margin-top:28px;padding-top:20px}summary{cursor:pointer;font-weight:600;padding:8px 0}.advanced-body{display:grid;gap:12px;padding-top:12px}.json-editor{min-height:230px;font-family:var(--code-font-family,ui-monospace,monospace)}.validation{font-size:12px}.validation.valid{color:var(--success-color,#0f9d58)}.validation.invalid,.inline-error{color:var(--error-color,#db4437)}.section-actions{margin-top:24px}.chart-heading{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}.chart-heading h2{margin:0;font-size:20px}.chart-legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--secondary-text-color);font-size:12px}.chart-legend span{display:flex;align-items:center;gap:6px}.legend-swatch{width:11px;height:11px;border-radius:3px}.legend-swatch.uncached,.chart-segment.uncached{background:var(--primary-color)}.legend-swatch.cached,.chart-segment.cached{background:var(--accent-color,var(--warning-color,#f9ab00))}.chart{height:190px;display:flex;align-items:end;gap:5px;border-bottom:1px solid var(--divider-color);padding-top:32px}.chart-column{position:relative;display:flex;flex:1;flex-direction:column;justify-content:flex-end;min-width:4px;max-width:28px;border-radius:4px 4px 0 0;cursor:help}.chart-segment{display:block;width:100%;min-height:0}.chart-segment:first-child{border-radius:4px 4px 0 0}.chart-column:hover:after,.chart-column:focus-visible:after{content:attr(data-tooltip);position:absolute;z-index:5;left:50%;bottom:calc(100% + 8px);width:max-content;max-width:min(300px,80vw);transform:translateX(-50%);padding:7px 9px;border-radius:7px;background:var(--primary-text-color);color:var(--card-background-color);font-size:11px;line-height:1.35;white-space:normal;box-shadow:0 5px 16px rgba(0,0,0,.25);pointer-events:none}.chart-column:first-child:hover:after,.chart-column:first-child:focus-visible:after{left:0;transform:none}.chart-column:last-child:hover:after,.chart-column:last-child:focus-visible:after{right:0;left:auto;transform:none}.chart-note{margin:10px 0 0;color:var(--secondary-text-color);font-size:12px}.table{overflow:auto}table{border-collapse:collapse;width:100%;margin-top:12px}th,td{text-align:left;border-bottom:1px solid var(--divider-color);padding:11px;white-space:nowrap}.empty{text-align:center;color:var(--secondary-text-color);padding:34px 18px}.error{background:var(--error-color,#db4437);color:#fff;padding:15px;border-radius:9px}.loading{display:flex;align-items:center;justify-content:center;gap:10px;min-height:130px;color:var(--secondary-text-color)}.spinner{width:20px;height:20px;border:2px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}dialog{color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:14px;padding:0;width:min(620px,calc(100vw - 28px));max-height:calc(100vh - 28px);box-shadow:0 16px 50px rgba(0,0,0,.35)}dialog.wide{width:min(900px,calc(100vw - 28px))}dialog::backdrop{background:rgba(0,0,0,.5)}dialog form{margin:0}.dialog-header{padding:18px 22px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--divider-color)}.dialog-header h2{margin:0;font-size:20px}.dialog-body{padding:22px;display:grid;gap:18px;overflow:auto;max-height:calc(100vh - 155px)}.dialog-actions{justify-content:flex-end;padding:14px 22px 18px;border-top:1px solid var(--divider-color)}.dialog-actions>.danger:first-child{margin-right:auto}.short-textarea{min-height:80px}.knowledge-editor{height:52vh;min-height:320px;max-height:65vh;font-family:var(--code-font-family,ui-monospace,monospace)}#memory-content{min-height:150px}.counter{text-align:right;font-size:12px;margin-top:-12px}.inline-error:empty{display:none}.session-body{gap:16px}.turn{display:grid;gap:9px;border-bottom:1px solid var(--divider-color);padding-bottom:18px}.message{padding:13px 15px;border-radius:10px;background:var(--secondary-background-color)}.message.assistant{border-left:3px solid var(--primary-color)}.message.user{border-left:3px solid var(--accent-color,var(--warning-color,#f9ab00))}.message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0;line-height:1.55}.toast{position:fixed;right:24px;bottom:24px;z-index:10000;max-width:min(460px,calc(100vw - 32px));padding:13px 17px;border-radius:9px;background:var(--success-color,#0f9d58);color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transform:translateY(12px);pointer-events:none;transition:.2s}.toast.visible{opacity:1;transform:none}.toast.toast-error{background:var(--error-color,#db4437)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--secondary-background-color);padding:14px;border-radius:9px}code{font-family:var(--code-font-family,ui-monospace,monospace)}
    .config-toolbar{display:flex;gap:16px;justify-content:space-between;align-items:center}.config-toolbar>input{max-width:620px}.agent-actions{display:flex;gap:10px;flex-wrap:wrap}.action-help{margin:-18px 0 0;text-align:right;color:var(--secondary-text-color);font-size:12px}.config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.config-card{align-self:start}.config-span{grid-column:1/-1}.config-stack{display:grid;gap:16px}.config-toggle{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:48px}.config-toggle>span{display:grid;gap:4px}.config-toggle input{width:42px;height:24px;min-height:24px;accent-color:var(--primary-color)}.dependent{display:grid;gap:16px;padding:4px 0 4px 18px;border-left:3px solid var(--divider-color)}.hidden{display:none!important}.prompt-editor{min-height:46vh;max-height:70vh;font-family:var(--code-font-family,ui-monospace,monospace);tab-size:2}.yaml-editor{min-height:240px;font-family:var(--code-font-family,ui-monospace,monospace);tab-size:2}.yaml-editor.tall{min-height:48vh}.editor-meta{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:10px;color:var(--secondary-text-color)}.compact-button{min-height:36px;padding:6px 11px}.subsection{border-top:1px solid var(--divider-color);margin-top:24px;padding-top:20px;display:grid;gap:14px}.rule-list{display:grid;gap:12px;margin-bottom:12px}.rule-row{border:1px solid var(--divider-color);border-radius:10px;padding:14px;display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:end}.rule-actions,.mode-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.rule-actions button{min-width:40px;padding:7px}.preview-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.save-bar{position:sticky;bottom:12px;z-index:5;display:flex;justify-content:space-between;align-items:center;gap:18px;margin-top:4px;padding:14px 18px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.2)}.dirty-state{color:var(--secondary-text-color)}.field-error{min-height:0;color:var(--error-color,#db4437);font-size:12px}.field-error:empty{display:none}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
    @media(max-width:850px){:host{padding:20px}header{align-items:stretch;flex-direction:column;gap:20px}.agent-picker{width:100%}.form-grid,.config-grid,.preview-grid{grid-template-columns:1fr}.config-span{grid-column:auto}.config-toolbar{align-items:stretch;flex-direction:column}.config-toolbar>input{max-width:none}.scope-bar small{margin-left:0}.list-card{grid-template-columns:1fr}.actions{justify-content:flex-start}.rule-row{grid-template-columns:1fr}.save-bar{bottom:8px}}
    @media(max-width:600px){:host{padding:12px}.page-heading h1{font-size:26px}nav{margin-inline:-12px;padding-inline:4px}.content-card{padding:18px}.section-heading{flex-direction:column;align-items:stretch}.section-heading button{width:100%}.search-row{flex-direction:column}.scope-bar{padding:16px}.knowledge-editor{height:50vh;min-height:260px}.dialog-body{padding:18px}.dialog-header,.dialog-actions{padding-inline:18px}.actions button{flex:1}.secondary-danger{margin-left:0}}
    button:disabled{cursor:not-allowed}.config-toolbar{margin-bottom:10px}.config-jumps{display:flex;align-items:center;gap:9px;overflow-x:auto;border:0;margin:0 0 18px;padding:7px 2px 10px;scrollbar-width:thin}.config-jumps a{color:var(--primary-color);font-size:13px;text-decoration:none;white-space:nowrap}.config-jumps a:hover{text-decoration:underline}.config-jumps span{color:var(--divider-color)}.config-surface{padding:0 30px 96px;display:block;scroll-behavior:smooth}.config-section{padding:34px 0;border-bottom:1px solid var(--divider-color);scroll-margin-top:18px;transition:opacity .15s}.config-section:last-of-type{border-bottom:0}.config-section-heading{margin-bottom:22px}.config-section-heading .eyebrow{margin:0;color:var(--primary-text-color);font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.config-section-heading>p:last-child,.subheading p{margin:6px 0 0;color:var(--secondary-text-color);line-height:1.5}.general-grid{grid-template-columns:repeat(3,minmax(180px,1fr))}.setting{display:grid;gap:7px;font-size:13px;color:var(--secondary-text-color);transition:opacity .15s}.setting-copy{display:grid;gap:4px;line-height:1.35}.setting-label-row{display:flex;align-items:center;gap:7px;min-width:0}.setting-label-row>label{display:block}.setting-label-row h2,.setting-label-row h3{min-width:0}.config-toggle{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:9px 0}.help-button{display:inline-grid;place-items:center;flex:0 0 21px;width:21px;min-width:21px;height:21px;min-height:21px;padding:0;border:1px solid var(--divider-color);border-radius:50%;background:transparent;color:var(--secondary-text-color);font-size:12px;font-weight:700;line-height:1}.help-button:hover{border-color:var(--primary-color);color:var(--primary-color);background:var(--secondary-background-color)}.help-button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}.help-popover{position:fixed;width:min(380px,calc(100vw - 24px));max-height:min(560px,calc(100vh - 24px));margin:0;padding:0;overflow:hidden;border:1px solid var(--divider-color);border-radius:12px;box-shadow:0 12px 38px rgba(0,0,0,.3)}.help-popover::backdrop{background:rgba(0,0,0,.14)}.help-popover-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 17px;border-bottom:1px solid var(--divider-color)}.help-popover-header h2{margin:0;font-size:17px}.help-close{display:grid;place-items:center;flex:0 0 32px;width:32px;min-width:32px;height:32px;min-height:32px;padding:0;background:transparent;color:var(--secondary-text-color);font-size:22px}.help-popover-content{padding:16px 17px 18px;overflow:auto;color:var(--primary-text-color);font-size:13px;line-height:1.5}.help-popover-content p{margin:0 0 12px}.help-popover-content dl{display:grid;gap:11px;margin:0 0 14px}.help-popover-content dl div{display:grid;gap:2px}.help-popover-content dt{font-weight:700}.help-popover-content dd{margin:0;color:var(--secondary-text-color)}.help-popover-content pre{margin:12px 0;padding:10px 12px}.help-link{display:inline-block;color:var(--primary-color);font-weight:600;text-decoration:none}.help-link:hover{text-decoration:underline}.switch-control{position:relative;display:block;width:44px;height:26px;flex:0 0 44px}.switch-control input{position:absolute;inset:0;z-index:2;width:44px;height:26px;min-height:0;margin:0;opacity:0;cursor:pointer}.switch-track{display:block;width:44px;height:26px;border-radius:14px;background:var(--disabled-text-color,#9e9e9e);transition:.18s}.switch-track:after{content:"";display:block;width:20px;height:20px;margin:3px;border-radius:50%;background:var(--card-background-color);box-shadow:0 1px 3px rgba(0,0,0,.35);transition:.18s}.switch-control input:checked+.switch-track{background:var(--primary-color)}.switch-control input:checked+.switch-track:after{transform:translateX(18px)}.switch-control input:focus-visible+.switch-track{outline:2px solid var(--primary-color);outline-offset:2px}.switch-control input:disabled+.switch-track{opacity:.45}.dependent{display:grid;gap:16px;padding:6px 0 6px 20px;border-left:2px solid var(--divider-color);transition:opacity .15s}.dependent.is-disabled{opacity:.5}.mappings-setting{margin-top:22px}.mappings-editor{min-height:150px}.prompt-setting{display:block}.prompt-editor{min-height:52vh;max-height:none}.setting-group{display:grid;gap:12px;padding-top:8px}.subheading h3{font-size:15px;margin:0}.rule-headings,.rule-row{display:grid;grid-template-columns:minmax(220px,1fr) minmax(220px,1fr) auto;gap:12px}.rule-headings{padding:0 12px;color:var(--secondary-text-color);font-size:12px}.rule-row{align-items:start;border:0;border-top:1px solid var(--divider-color);border-radius:0;padding:12px 0}.rule-list{gap:0;margin:0}.mobile-label{display:none}.rule-actions{padding-top:1px;flex-wrap:nowrap}.rule-actions button{min-height:40px}.add-regex{justify-self:start}.preview-button,.section-reset{margin-top:14px}.search-dim{opacity:.24}.save-bar{margin:28px -10px -72px}.tools-surface{padding:26px}.tool-title{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.type-badge,.disabled-badge{display:inline-flex;padding:3px 8px;border-radius:999px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.disabled-badge{background:color-mix(in srgb,var(--error-color,#db4437) 12%,transparent);color:var(--error-color,#db4437);font-weight:700}.tool-card{padding:15px 17px;transition:opacity .15s}.tool-card.is-disabled{opacity:.62}.tool-card-actions{align-items:center}.tool-enabled-control{display:flex;align-items:center;gap:8px;color:var(--secondary-text-color);font-size:12px}.tools-actions{justify-content:flex-end}.tools-actions .validation{margin-right:auto}.dialog-meta{margin:5px 0 0;color:var(--secondary-text-color);font-size:12px}.tool-dialog{width:min(1180px,calc(100vw - 28px));height:min(850px,calc(100vh - 28px));max-height:calc(100vh - 28px)}.tool-dialog-body{display:grid;grid-template-rows:auto minmax(0,1fr) auto;gap:12px;height:calc(100% - 142px);max-height:none;padding:18px 22px;min-width:0}.built-in-picker{display:grid;grid-template-columns:minmax(190px,280px) minmax(240px,1fr);align-items:center;gap:6px 14px}.built-in-picker label{font-weight:700}.built-in-picker small{grid-column:1/-1;color:var(--secondary-text-color)}.tool-editor-label{height:100%;min-height:0;min-width:0;width:100%}.tool-yaml-editor{height:100%;min-height:300px;min-width:0;max-width:100%;resize:none;white-space:pre;overflow:auto;line-height:1.55}.tool-dialog .dialog-actions{margin-top:auto}.mode-row input{width:auto;min-height:auto}.mode-row label{display:flex;grid-gap:7px;align-items:center}
    .function-groups-help{margin-bottom:18px}.tool-search{display:block;margin-bottom:18px}.function-groups{display:grid;gap:18px}.function-group-card{border:1px solid var(--divider-color);border-radius:12px;padding:18px;background:var(--card-background-color)}.function-group-heading{display:flex;justify-content:space-between;align-items:start;gap:20px}.function-group-heading h3,.tool-title h4{margin:0}.function-group-heading p{margin:7px 0;color:var(--secondary-text-color);line-height:1.45}.function-group-heading code{font-size:12px;color:var(--secondary-text-color)}.availability-badge,.function-count{display:inline-flex;padding:4px 9px;border-radius:999px;background:color-mix(in srgb,var(--success-color,#0f9d58) 15%,transparent);color:var(--success-color,#0f9d58);font-size:11px;font-weight:600}.availability-badge.on-demand{background:color-mix(in srgb,var(--primary-color) 14%,transparent);color:var(--primary-color)}.function-count{background:var(--secondary-background-color);color:var(--secondary-text-color)}.function-group-card details{margin-top:14px;padding-top:10px}.function-group-card summary{min-height:40px}.function-group-card .tool-list{padding-top:10px}.group-dialog{width:min(820px,calc(100vw - 28px))}.group-dialog-body{max-height:calc(100vh - 170px)}.group-functions-fieldset{margin:0;padding-top:18px}.group-function-choices{display:grid;gap:8px;max-height:300px;overflow:auto;margin-top:12px}.group-function-choice{display:grid;grid-template-columns:auto 1fr;align-items:start;gap:11px;padding:11px;border:1px solid var(--divider-color);border-radius:9px;color:var(--primary-text-color);cursor:pointer}.group-function-choice.is-disabled{opacity:.58}.group-function-choice:hover{background:var(--secondary-background-color)}.group-function-choice input{width:18px;min-height:18px;margin-top:2px}.group-function-choice span{display:grid;gap:3px}.group-function-choice small{overflow-wrap:anywhere}
    .rule-list{display:grid;gap:14px}.request-rule-card{border:1px solid var(--divider-color);border-radius:14px;padding:18px;background:var(--card-background-color)}.request-rule-card.disabled{opacity:.62}.rule-card-heading{display:flex;justify-content:space-between;gap:16px;align-items:start}.rule-card-heading h2{font-size:18px;margin:6px 0 0}.type-badge{display:inline-flex;border-radius:999px;padding:4px 9px;font-size:11px;font-weight:700}.type-badge.local{color:var(--success-color,#0f9d58);background:color-mix(in srgb,var(--success-color,#0f9d58) 14%,transparent)}.type-badge.routing{color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 14%,transparent)}.phrase-chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:14px}.phrase-chips span{padding:6px 9px;border-radius:8px;background:var(--secondary-background-color);font-size:13px}.phrase-chips b{font-weight:700;margin-right:4px}.sensitive-warning{color:var(--warning-color,#b26a00);font-weight:600}.request-rule-card>.actions{display:flex;gap:8px;justify-content:flex-end}.rule-settings details{padding-block:8px}.rule-settings details+details{border-top:1px solid var(--divider-color)}.rule-settings details>summary{font-weight:700;cursor:pointer}.matching-settings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}.fuzzy-sensitivity.is-disabled{opacity:.5}.wording-group{display:grid;grid-template-columns:1fr 2fr auto;gap:10px;align-items:end;margin-top:10px}.ha-action-row{display:grid;grid-template-columns:2fr 1fr 2fr auto;gap:10px;align-items:end;border:1px solid var(--divider-color);border-radius:10px;padding:12px;margin-bottom:10px}.ha-action-row label{margin:0}.ha-action-row ha-selector{display:block;min-width:0}.ha-service-fields{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.ha-action-advanced{grid-column:1/-1}.ha-action-advanced summary{cursor:pointer;font-weight:600}.ha-action-advanced textarea{min-height:140px;font-family:var(--code-font-family,ui-monospace,monospace)}.request-rule-dialog section{border-top:1px solid var(--divider-color);margin-top:20px;padding-top:16px}.request-rule-dialog h3{margin:0 0 8px}
    .editor-meta-actions,.prompt-section-badges{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.advanced-context-formatting{border:1px solid var(--divider-color);border-radius:10px;padding:12px 14px}.advanced-context-formatting summary{cursor:pointer;font-weight:600}.advanced-context-formatting[open] summary{margin-bottom:12px}.prompt-preview-dialog{height:min(900px,calc(100vh - 28px))}.prompt-preview-body{grid-template-rows:auto auto minmax(260px,1fr) auto auto;max-height:none;height:calc(100% - 142px)}.request-preview-metrics{display:grid;gap:5px;padding:12px 14px;border-radius:10px;background:var(--secondary-background-color)}.request-preview-metrics span{color:var(--secondary-text-color);font-size:13px}.request-preview-sections{display:grid;align-content:start;gap:10px;min-height:0;overflow:auto}.request-preview-section{border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color)}.request-preview-section summary{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 14px;cursor:pointer;font-weight:600}.request-section-meta{display:flex;align-items:center;gap:8px;color:var(--secondary-text-color);font-size:12px;font-weight:400}.request-preview-output{box-sizing:border-box;width:calc(100% - 24px);min-height:240px;margin:0 12px 12px;resize:vertical;white-space:pre;overflow:auto;line-height:1.45}.prompt-preview-notes{margin:0;padding-left:20px;color:var(--secondary-text-color);font-size:12px;line-height:1.5}.prompt-preview-notes:empty{display:none}
    .mobile-nav,.mobile-local-nav{display:none}.top-nav{overflow:visible}.global-search{position:relative;max-width:720px;margin:0 0 26px}.search-results{position:absolute;z-index:20;top:calc(100% + 6px);left:0;right:0;display:grid;max-height:min(520px,70vh);overflow:auto;padding:8px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;box-shadow:0 12px 32px rgba(0,0,0,.28)}.settings-result{display:grid;gap:3px;text-align:left;background:transparent;color:var(--primary-text-color);border-radius:8px}.settings-result:hover{background:var(--secondary-background-color)}.settings-result span,.settings-result small{color:var(--secondary-text-color)}.section-layout.has-local-nav{display:grid;grid-template-columns:210px minmax(0,1fr);gap:28px;align-items:start}.section-layout main{min-width:0}.local-nav{position:sticky;top:16px;display:grid;gap:4px;overflow:visible;margin:0;padding:8px;border:1px solid var(--divider-color);border-radius:12px;background:var(--card-background-color)}.local-nav button{min-height:38px;padding:9px 11px;text-align:left;border-radius:8px;border:0;white-space:normal}.local-nav button.active{border:0;background:color-mix(in srgb,var(--primary-color) 13%,transparent);font-weight:600}.page-intro{display:grid;gap:7px}.page-intro h1,.page-intro p{margin:0}.page-intro p{color:var(--secondary-text-color);line-height:1.5}.dashboard-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.dashboard-card{display:flex;justify-content:space-between;align-items:end;gap:18px;padding:22px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:13px}.dashboard-card h2,.dashboard-card p{margin:0}.dashboard-card strong{display:block;margin-top:12px;font-size:18px}.dashboard-card p{margin-top:7px;color:var(--secondary-text-color);line-height:1.45}.guide-search{display:block}.guide-topics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.guide-topic{margin:0;padding:17px}.guide-topic summary{display:block}.guide-topic summary span,.guide-topic summary small{display:grid;gap:6px}.guide-topic-body{padding-top:10px}.comparison-table{overflow:auto}.inline-route{margin-top:18px}.overview-warnings{display:grid;gap:12px}
    .guide-link{justify-self:start;min-height:34px;padding:5px 0;background:transparent;color:var(--primary-color);font-weight:600}.guide-link:hover{text-decoration:underline}
    @media(min-width:680px) and (max-width:1100px){.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.general-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.dashboard-grid,.guide-topics{grid-template-columns:1fr}}
    @media(max-width:760px){.top-nav,.local-nav{display:none}.mobile-nav,.mobile-local-nav{display:grid;margin-bottom:18px}.section-layout.has-local-nav{display:block}.dashboard-grid,.guide-topics{grid-template-columns:1fr}.dashboard-card{align-items:stretch;flex-direction:column}.dashboard-card button{width:100%}.global-search{max-width:none}.comparison-table table{min-width:720px}}
    @media(max-width:679px){.form-grid,.general-grid{grid-template-columns:1fr}.config-surface{padding:0 18px 92px}.config-section{padding:28px 0}.config-jumps{margin-inline:-4px}.rule-headings{display:none}.rule-row{grid-template-columns:1fr}.mobile-label{display:block}.rule-actions{padding-top:0}.save-bar{align-items:stretch;flex-direction:column;bottom:6px;margin-inline:-8px}.save-bar .actions{display:grid;grid-template-columns:1fr 1fr}.tool-dialog,.group-dialog{box-sizing:border-box;left:6px;top:6px;margin:0;width:calc(100vw - 12px);max-width:calc(100vw - 12px);height:calc(100vh - 12px);max-height:calc(100vh - 12px);overflow:hidden}.tool-dialog-body{padding:12px}.built-in-picker{grid-template-columns:1fr}.built-in-picker small{grid-column:auto}.tool-yaml-editor{min-height:0}.tools-surface{padding:18px}.tools-actions{justify-content:stretch}.tools-actions button{flex:1}.tools-actions .validation{width:100%;order:-1}.function-group-heading{display:grid}.function-group-heading .actions{display:grid;grid-template-columns:1fr 1fr}.group-dialog-body{padding:14px}.group-function-choices{max-height:34vh}.help-popover{left:8px!important;right:8px;top:auto!important;bottom:8px;width:auto;max-width:none;max-height:min(70vh,560px);border-radius:14px}.ha-action-row{grid-template-columns:1fr}.request-rule-card>.actions{display:grid;grid-template-columns:1fr 1fr}.request-rule-card>.actions .rule-delete{grid-column:1/-1}.request-rule-dialog{box-sizing:border-box;left:6px;top:6px;margin:0;width:calc(100vw - 12px);max-width:calc(100vw - 12px);height:calc(100vh - 12px);max-height:calc(100vh - 12px)}}
    .page-shell{max-width:1380px}.section-layout{display:block}.section-selector{display:grid;grid-template-columns:minmax(240px,420px) minmax(0,620px);align-items:end;gap:18px;margin:0 0 28px}.section-selector p{margin:0 0 10px;color:var(--secondary-text-color);line-height:1.45}.config-toolbar{justify-content:flex-end}.agent-actions-menu{position:relative;margin:0;padding:0;border:0}.agent-actions-menu>summary{min-height:42px;padding:10px 16px;border:1px solid var(--primary-color);border-radius:9px;color:var(--primary-color);list-style:none}.agent-actions-menu>summary:after{content:" ▾"}.agent-actions-menu>summary::-webkit-details-marker{display:none}.agent-actions-menu>div{position:absolute;z-index:15;right:0;top:calc(100% + 6px);display:grid;gap:5px;min-width:230px;padding:8px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color);box-shadow:0 10px 28px rgba(0,0,0,.25)}.agent-actions-menu button{text-align:left}.switch-track{position:relative}.switch-track:after{position:absolute;left:3px;top:50%;margin:0;transform:translateY(-50%)}.switch-control input:checked+.switch-track:after{transform:translate(18px,-50%)}.page-intro{max-width:780px}.local-nav,.mobile-local-nav{display:none!important}
    .scope-bar{max-width:1380px}
    .access-explainer{display:grid;gap:24px}.access-explainer h2,.access-explainer p{margin:0}.access-explainer p{margin-top:7px;max-width:780px;color:var(--secondary-text-color);line-height:1.5}.compact-status{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:17px 0;border-block:1px solid var(--divider-color)}.compact-status>span{display:grid;gap:5px}.compact-status small{max-width:780px;line-height:1.45}.status-value{padding:5px 10px;border-radius:999px;background:var(--secondary-background-color)}.status-value.on{color:var(--success-color,#0f9d58)}
    @media(max-width:760px){.section-selector{grid-template-columns:1fr;gap:7px}.section-selector p{margin:0}.agent-actions-menu,.agent-actions-menu>summary{width:100%}.agent-actions-menu>div{left:0;right:0}}
    .matching-setting{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;min-width:0;padding:10px 0}.matching-copy{display:grid;gap:4px;min-width:0;line-height:1.4}.matching-title{color:var(--primary-text-color);font-weight:600}.matching-copy small{line-height:1.45}.matching-setting>input{flex:0 0 42px;width:42px;min-height:24px;margin-top:2px}.matching-control{flex:0 0 min(180px,42%);min-width:120px}.matching-control select{min-height:38px}.fuzzy-sensitivity.is-disabled .matching-copy{opacity:.65}
    @media(max-width:679px){.matching-settings,.ha-service-fields{grid-template-columns:1fr}.matching-setting{gap:12px}.matching-control{flex-basis:min(150px,42%);min-width:110px}.wording-group{grid-template-columns:1fr auto}.wording-group label:nth-child(2){grid-column:1/-1}}
    :host{font-size:14px;line-height:1.45;background:color-mix(in srgb,var(--secondary-background-color) 42%,var(--primary-background-color))}
    label,.setting{font-size:14px}
    .content-card,.metric,.scope-bar{border-color:color-mix(in srgb,var(--divider-color) 74%,var(--secondary-text-color))}
    .content-card,.metric{box-shadow:0 1px 2px rgba(0,0,0,.035)}
    .metric{background:color-mix(in srgb,var(--secondary-background-color) 18%,var(--card-background-color))}
    .meta,.validation,.counter,.action-help,.dialog-meta,.chart-note,.chart-legend,.request-section-meta,.prompt-preview-notes{font-size:13px}
    .setting,.help-popover-content{font-size:14px}
    .config-jumps a{font-size:14px}
    .rule-headings{font-size:13px}
    .chart-column:hover:after,.chart-column:focus-visible:after{font-size:12px}
    .chart-axis{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:7px;color:var(--secondary-text-color);font-size:13px}
    .chart-axis span:nth-child(2){text-align:center}.chart-axis span:last-child{text-align:right}
  `; }
}

customElements.define("extended-openai-management-panel", ExtendedOpenAIManagementPanel);
