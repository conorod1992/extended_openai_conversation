const clone = (value) => JSON.parse(JSON.stringify(value));
const settingSearch = (label, description, key) => `${label} ${description} ${key}`.toLowerCase();

function option(panel, item, selected) {
  const value = typeof item === "string" ? item : item.value;
  const label = typeof item === "string" ? String(item).replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase()) : item.label;
  return `<option value="${panel._e(value)}" ${value === selected ? "selected" : ""}>${panel._e(label)}</option>`;
}

function select(panel, key, label, value, description) {
  const choices = panel._result?.options?.[key] || [];
  return `<div class="setting" data-setting data-field="${key}" data-search="${panel._e(settingSearch(label, description, key))}"><span class="setting-label-row"><label for="config-${key}">${panel._e(label)}</label></span><select id="config-${key}" data-memory-config="${key}">${choices.map((item) => option(panel, item, value)).join("")}</select><small>${panel._e(description)}</small><span class="field-error" data-error="${key}"></span></div>`;
}

function numberField(panel, key, label, value, description, min = null, max = null) {
  return `<div class="setting" data-setting data-field="${key}" data-search="${panel._e(settingSearch(label, description, key))}"><span class="setting-label-row"><label for="config-${key}">${panel._e(label)}</label></span><input id="config-${key}" data-memory-config="${key}" data-type="number" type="number" value="${panel._e(value)}" ${min === null ? "" : `min="${min}"`} ${max === null ? "" : `max="${max}"`}><small>${panel._e(description)}</small><span class="field-error" data-error="${key}"></span></div>`;
}

function textField(panel, key, label, value, description, disabled = false) {
  return `<div class="setting" data-setting data-field="${key}" data-search="${panel._e(settingSearch(label, description, key))}"><span class="setting-label-row"><label for="config-${key}">${panel._e(label)}</label></span><input id="config-${key}" data-memory-config="${key}" type="text" value="${panel._e(value || "")}" ${disabled ? "disabled" : ""}><small>${panel._e(description)}</small><span class="field-error" data-error="${key}"></span></div>`;
}

function saveBar(panel) {
  if (!panel._configDirty) return "";
  return `<div class="save-bar"><strong class="dirty-state">Unsaved changes</strong><div class="actions"><button type="button" class="secondary" id="memory-settings-revert">Revert</button><button type="button" id="memory-settings-save">Save configuration</button></div></div>`;
}

export function renderMemorySettings(panel) {
  const config = panel._draft || panel._result?.config || {};
  const hybrid = config.memory_retrieval_mode === "hybrid";
  return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="memory">Learn about memory</button>
    <div class="content-card config-surface">
      <section id="config-memory" class="config-section" data-config-section data-search="memory persistent temporary short term long term automatic retrieval embeddings shared household">
        <div class="config-section-heading"><p class="eyebrow">Memory settings</p><p>Choose what the assistant may remember, how relevant memories are found, and whether household memories are available.</p></div>
        <div class="config-stack">
          <div class="setting-group"><div class="subheading"><h3>Personal memory</h3><p>Control durable memories and automatically expiring short-term details.</p></div>
            ${select(panel, "memory_mode", "Long-term memory", config.memory_mode, "Choose whether durable memories are off, saved only when explicitly requested, or may also be created automatically.")}
            ${select(panel, "temporary_memory", "Short-term memory", config.temporary_memory, "Choose how readily useful temporary details are remembered until they expire automatically.")}
          </div>
          <div class="setting-group"><div class="subheading"><h3>Retrieval</h3><p>Control which stored memories are supplied automatically and how relevance is calculated.</p></div>
            ${numberField(panel, "memory_auto_retrieve_limit", "Automatically include memories", config.memory_auto_retrieve_limit, "Select up to this many relevant memories when a new conversation starts. Set to 0 to use long-term memory only on demand.", 0, 10)}
            ${select(panel, "memory_retrieval_mode", "Memory retrieval", config.memory_retrieval_mode, "Lexical retrieval is local and dependency-free. Hybrid retrieval combines lexical matching with semantic embeddings.")}
            <div class="dependent ${hybrid ? "" : "is-disabled"}" data-memory-hybrid>${textField(panel, "memory_embedding_model", "Embedding model", config.memory_embedding_model || "text-embedding-3-small", "Used only for Hybrid retrieval. The configured provider must support embeddings.", !hybrid)}</div>
          </div>
          <div class="setting-group"><div class="subheading"><h3>Shared household memory</h3><p>Keep household-wide memory behavior separate from private user memories.</p></div>
            ${select(panel, "shared_memory_mode", "Shared household memory", config.shared_memory_mode, "Choose whether shared memories are disabled, saved only when explicitly requested, or may also be created automatically.")}
          </div>
          <div class="notice"><strong>Looking for stored memories?</strong><p>This page controls memory behavior. Use Memories to review, add, edit, reassign, or remove stored items.</p><button type="button" class="secondary inline-route" data-page="data-memory" data-subsection="memories">Manage stored memories</button></div>
        </div>
      </section>
      ${saveBar(panel)}<span id="memory-settings-save-anchor" class="sr-only"></span>
    </div>`;
}

function readMemorySettings(panel) {
  const config = clone(panel._draft || panel._result?.config || {});
  panel.shadowRoot.querySelectorAll("[data-memory-config]").forEach((input) => {
    let value = input.value;
    if (input.dataset.type === "number") value = Number(value);
    config[input.dataset.memoryConfig] = value;
  });
  panel._draft = config;
  return config;
}

function showErrors(panel, errors = {}) {
  panel.shadowRoot.querySelectorAll(".field-error").forEach((item) => { item.textContent = ""; });
  for (const [key, message] of Object.entries(errors)) {
    const target = panel.shadowRoot.querySelector(`[data-error="${CSS.escape(key)}"]`);
    if (target) target.textContent = message;
  }
}

function bindSaveControls(panel) {
  const root = panel.shadowRoot;
  root.querySelector("#memory-settings-revert")?.addEventListener("click", () => {
    panel._draft = clone(panel._configData.config);
    panel._draftTitle = panel._configData.title;
    panel._setConfigDirty(false);
    panel._render();
  });
  root.querySelector("#memory-settings-save")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    panel._setSaving?.(button, true);
    try {
      const config = readMemorySettings(panel);
      const validation = await panel._call("configuration", "validate", {config});
      showErrors(panel, validation.errors);
      if (!validation.valid) {
        panel._toast("Fix the highlighted configuration errors", true);
        return;
      }
      const saved = await panel._call("configuration", "update", {config, title: panel._draftTitle});
      panel._configData = {...panel._configData, ...saved};
      panel._result = panel._configData;
      panel._draft = clone(saved.config);
      panel._draftTitle = saved.title;
      panel._setConfigDirty(false);
      panel._toast("Configuration saved");
      await panel._loadAgents(panel._agentId);
    } catch (err) {
      panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
    } finally {
      panel._setSaving?.(button, false);
    }
  });
}

function ensureSaveBar(panel) {
  if (panel.shadowRoot.querySelector("#memory-settings-save")) return;
  panel.shadowRoot.querySelector("#memory-settings-save-anchor")?.insertAdjacentHTML("beforebegin", saveBar(panel));
  bindSaveControls(panel);
}

function updateHybridState(panel) {
  const root = panel.shadowRoot;
  const hybrid = root.querySelector('[data-memory-config="memory_retrieval_mode"]')?.value === "hybrid";
  const container = root.querySelector("[data-memory-hybrid]");
  const input = root.querySelector('[data-memory-config="memory_embedding_model"]');
  container?.classList.toggle("is-disabled", !hybrid);
  if (input) input.disabled = !hybrid;
}

export function bindMemorySettings(panel) {
  const root = panel.shadowRoot;
  bindSaveControls(panel);
  root.querySelectorAll("[data-memory-config]").forEach((input) => input.addEventListener("input", () => {
    readMemorySettings(panel);
    panel._setConfigDirty(true);
    ensureSaveBar(panel);
    if (input.dataset.memoryConfig === "memory_retrieval_mode") updateHybridState(panel);
  }));
}
