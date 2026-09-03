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
  const enabled = Boolean(status?.enabled);
  return `<section class="content-card knowledge-availability-setting">
    <div class="config-toggle setting">
      <span class="setting-copy"><span class="setting-label-row"><label for="knowledge-enabled-toggle"><strong>Allow the assistant to use Knowledge</strong></label></span><small>When off, stored sources remain in the library but Knowledge tools are not available to the assistant. Changes here save immediately.</small></span>
      <label class="switch-control" for="knowledge-enabled-toggle"><input id="knowledge-enabled-toggle" type="checkbox" role="switch" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></label>
    </div>
  </section>`;
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

export {knowledgeAvailabilityMarkup, stripLocalHandlingConfiguration, stripWebSkillsConfiguration};
