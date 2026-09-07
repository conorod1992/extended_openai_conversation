import {getAgentConfigModule} from "./agent-config-loader.js";

const PATCHED = Symbol.for("extended-openai.management-capabilities-ia");
const WEB_SKILLS_FIELDS = new Set(["web_search", "web_search_context", "skills"]);

function renderConfiguration(panel) {
  const module = getAgentConfigModule();
  if (!module) return panel._loading?.() || '<div class="loading">Loading configuration…</div>';
  return module.renderConfiguration(panel);
}

function bindConfiguration(panel) {
  return getAgentConfigModule()?.bindConfiguration(panel);
}

function transformConfiguration(html, transform, documentRef = globalThis.document) {
  if (!documentRef?.createElement) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  transform(template.content);
  return template.innerHTML;
}

function stripWebSkillsConfiguration(html, documentRef = globalThis.document) {
  return transformConfiguration(html, (root) => {
    const section = root.querySelector("#config-capabilities");
    if (!section) return;
    section.querySelectorAll("[data-field]").forEach((field) => {
      if (!WEB_SKILLS_FIELDS.has(field.dataset.field)) field.remove();
    });
    const heading = section.querySelector(".config-section-heading");
    if (heading) heading.innerHTML = "<p class=\"eyebrow\">Web search & Skills</p><p>Choose optional online information and installed instruction sets the assistant may load when needed.</p>";
  }, documentRef);
}

function stripLocalHandlingConfiguration(html, documentRef = globalThis.document) {
  return transformConfiguration(html, (root) => root.querySelector("#config-local")?.remove(), documentRef);
}

function knowledgeAvailabilityMarkup(panel) {
  if (panel._data?.is_admin === false) return "";
  const sectionStatus = panel._result?.feature_status;
  const status = sectionStatus && typeof sectionStatus.enabled === "boolean"
    ? sectionStatus
    : panel._selectedAgent?.()?.feature_status?.knowledge;
  const enabled = typeof status?.enabled === "boolean" ? status.enabled : status?.state === "enabled";
  return `<section class="content-card knowledge-availability-setting">
    <div class="config-toggle setting">
      <span class="setting-copy"><span class="setting-label-row"><label for="knowledge-enabled-toggle"><strong>Allow the assistant to use Knowledge</strong></label></span><small>When off, stored sources remain in the library but Knowledge tools are not available to the assistant. Changes here save immediately.</small></span>
      <label class="switch-control" for="knowledge-enabled-toggle"><input id="knowledge-enabled-toggle" type="checkbox" role="switch" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>
    </div>
  </section>`;
}

function knowledgeSourceAvailabilityBadge(source) {
  const enabled = source?.enabled !== false;
  return `<span class="${enabled ? "availability-badge" : "disabled-badge"} knowledge-source-availability-badge">${enabled ? "Available" : "Unavailable"}</span>`;
}

function decorateKnowledgeSources(panel, html, documentRef = globalThis.document) {
  return transformConfiguration(html, (root) => {
    const sources = panel._result?.sources || [];
    root.querySelectorAll(".edit-source[data-id]").forEach((card) => {
      const source = sources.find((item) => item.source_id === card.dataset.id);
      const heading = card.querySelector("h3");
      if (!source || !heading) return;
      heading.insertAdjacentHTML("afterend", knowledgeSourceAvailabilityBadge(source));
    });
  }, documentRef);
}

function addKnowledgeSourceAvailabilityControl(html, documentRef = globalThis.document) {
  return transformConfiguration(html, (root) => {
    const content = root.querySelector("#knowledge-content")?.closest("label");
    if (!content || root.querySelector("#knowledge-source-enabled")) return;
    const setting = documentRef.createElement("div");
    setting.className = "config-toggle setting knowledge-source-availability-setting";
    setting.innerHTML = `<span class="setting-copy"><span class="setting-label-row"><label for="knowledge-source-enabled"><strong>Available to the assistant</strong></label></span><small>Turn this off to keep the source stored locally without including it in Knowledge retrieval.</small></span><label class="switch-control" for="knowledge-source-enabled"><input id="knowledge-source-enabled" type="checkbox" role="switch" checked><span class="switch-track" aria-hidden="true"></span></label>`;
    content.before(setting);
  }, documentRef);
}

async function saveKnowledgeAvailability(panel, input) {
  const desired = input.checked;
  input.disabled = true;
  try {
    const current = await panel._call("configuration", "get");
    const config = JSON.parse(JSON.stringify(current.config || {}));
    config.knowledge_enabled = desired;
    const validation = await panel._call("configuration", "validate", {config});
    if (!validation.valid) throw new Error(Object.values(validation.errors || {})[0] || "Configuration validation failed");
    await panel._call("configuration", "update", {config, title: current.title});
    panel._clearConfigDraft?.();
    await panel._loadAgents(panel._agentId);
    await panel._loadSection(true);
    panel._toast(`Knowledge ${desired ? "enabled" : "disabled"}`);
  } catch (err) {
    input.checked = !desired;
    input.disabled = false;
    panel._toast(`Unable to update Knowledge: ${err.message || String(err)}`, true);
  }
}

export function installManagementCapabilitiesIA(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalIsDraftView = prototype._isDraftView;
    prototype._isDraftView = function(page = this._page, subsection = this._subsection) {
      if (page === "capabilities" && subsection === "web-skills") return this._data?.is_admin !== false;
      return originalIsDraftView.call(this, page, subsection);
    };

    const originalConfigSectionsForView = prototype._configSectionsForView;
    prototype._configSectionsForView = function() {
      const view = this._viewKey();
      if (view === "capabilities/home-assistant") return ["local"];
      if (view === "capabilities/web-skills") return ["capabilities"];
      return originalConfigSectionsForView.call(this);
    };

    const originalContent = prototype._content;
    prototype._content = function(agent) {
      const view = this._viewKey();
      if (view === "capabilities/home-assistant") {
        this._configSections = ["local"];
        return `${this._homeAssistant(agent)}${renderConfiguration(this)}`;
      }
      if (view === "capabilities/web-skills") {
        this._configSections = ["capabilities"];
        return stripWebSkillsConfiguration(renderConfiguration(this));
      }
      if (view === "assistant/conversation") {
        return stripLocalHandlingConfiguration(originalContent.call(this, agent));
      }
      if (view === "data-memory/knowledge") {
        return `${knowledgeAvailabilityMarkup(this)}${originalContent.call(this, agent)}`;
      }
      return originalContent.call(this, agent);
    };

    const originalKnowledge = prototype._knowledge;
    prototype._knowledge = function(...args) {
      return decorateKnowledgeSources(this, originalKnowledge.apply(this, args));
    };

    const originalDialogs = prototype._dialogs;
    prototype._dialogs = function(...args) {
      return addKnowledgeSourceAvailabilityControl(originalDialogs.apply(this, args));
    };

    const originalKnowledgeValues = prototype._knowledgeValues;
    prototype._knowledgeValues = function(...args) {
      const values = originalKnowledgeValues.apply(this, args);
      const input = this.shadowRoot?.querySelector("#knowledge-source-enabled");
      return {...values, enabled: input?.checked ?? true};
    };

    const originalSetKnowledgeEditorDisabled = prototype._setKnowledgeEditorDisabled;
    prototype._setKnowledgeEditorDisabled = function(disabled) {
      const result = originalSetKnowledgeEditorDisabled.call(this, disabled);
      const input = this.shadowRoot?.querySelector("#knowledge-source-enabled");
      if (input) input.disabled = disabled;
      return result;
    };

    const originalOpenKnowledge = prototype._openKnowledge;
    prototype._openKnowledge = async function(sourceId = null) {
      const input = this.shadowRoot?.querySelector("#knowledge-source-enabled");
      if (input) input.checked = true;
      const result = await originalOpenKnowledge.call(this, sourceId);
      const currentInput = this.shadowRoot?.querySelector("#knowledge-source-enabled");
      if (currentInput) currentInput.checked = sourceId ? this._editingSource?.enabled !== false : true;
      if (["create", "edit"].includes(this._knowledgeMode)) this._editorInitial = this._knowledgeValues();
      return result;
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this, args);
      const view = this._viewKey();
      if (["capabilities/home-assistant", "capabilities/web-skills"].includes(view)) bindConfiguration(this);
      const knowledgeToggle = this.shadowRoot.querySelector("#knowledge-enabled-toggle");
      knowledgeToggle?.addEventListener("change", () => saveKnowledgeAvailability(this, knowledgeToggle));
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementCapabilitiesIA();
}

export {
  addKnowledgeSourceAvailabilityControl,
  decorateKnowledgeSources,
  knowledgeAvailabilityMarkup,
  knowledgeSourceAvailabilityBadge,
  stripLocalHandlingConfiguration,
  stripWebSkillsConfiguration,
};
