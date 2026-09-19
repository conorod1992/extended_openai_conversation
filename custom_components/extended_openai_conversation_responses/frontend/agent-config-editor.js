import { bindBackupTransfer } from "./backup-transfer-ui.js";
import { bindExposedAttributeSettings } from "./exposed-attributes-ui.js";
const {ensureAgentConfigModule, getAgentConfigModule} = await import("./agent-config-loader.js");

if (typeof document === "undefined") await ensureAgentConfigModule();


export const BACKUP_CREDENTIAL_WARNING = "Recognised API keys, tokens, passwords, authorization headers and other common secrets are redacted from full backups. Re-enter any required credentials after restore. Redaction is best-effort, so review backup files before sharing them.";
const CACHEABLE_CONFIG_SECTIONS = new Set(["capabilities", "archive", "voice", "speech", "context", "retention", "backup"]);
const MAX_CONFIG_RENDER_CACHE_ENTRIES = 8;

export {reasoningEffortOptionsForResult} from "./agent-config-model-presentation.js";

export function isFunctionGroupEnabled(...args) {
  return requiredImplementation("isFunctionGroupEnabled").isFunctionGroupEnabled(...args);
}

function configRenderCacheKey(panel) {
  if (panel?._configDirty) return null;
  const sections = Array.isArray(panel?._configSections) ? panel._configSections : [];
  if (!sections.length || sections.some((section) => !CACHEABLE_CONFIG_SECTIONS.has(section))) return null;
  return `${panel._viewKey?.() || ""}|${sections.join("|")}`;
}

function getCachedConfigurationMarkup(panel, key, voiceIdentity) {
  const state = panel?._eocConfigRenderCache;
  if (!key || !configRenderCacheMatches(panel, state, voiceIdentity)) return null;
  return state.entries.get(key) ?? null;
}

function configRenderCacheMatches(panel, state, voiceIdentity) {
  return state && state.result === panel._result && state.draft === panel._draft
    && state.agentId === panel._agentId && state.modelData === panel._modelCatalogData
    && state.capabilities === panel._result?.model_capabilities
    && state.scopes === panel._baseScopes && state.dataScopes === panel._data?.scopes
    && state.voiceIdentity === voiceIdentity;
}

function rememberConfigurationMarkup(panel, key, html, voiceIdentity) {
  if (!key) return html;
  let state = panel._eocConfigRenderCache;
  if (!configRenderCacheMatches(panel, state, voiceIdentity)) {
    state = {result: panel._result, draft: panel._draft, agentId: panel._agentId,
      modelData: panel._modelCatalogData, capabilities: panel._result?.model_capabilities, scopes: panel._baseScopes, dataScopes: panel._data?.scopes, voiceIdentity, entries: new Map()};
    panel._eocConfigRenderCache = state;
  }
  if (state.entries.has(key)) state.entries.delete(key);
  state.entries.set(key, html);
  while (state.entries.size > MAX_CONFIG_RENDER_CACHE_ENTRIES) {
    state.entries.delete(state.entries.keys().next().value);
  }
  return html;
}

function requiredImplementation(name) {
  const module = getAgentConfigModule();
  if (!module) throw new Error(`Agent configuration module is not loaded before ${name}`);
  return module;
}

function queueRender(panel) {
  void ensureAgentConfigModule()
    .then(() => panel?._render?.())
    .catch((err) => {
      if (!panel) return;
      panel._error = `Unable to load configuration editor: ${err.message || String(err)}`;
      panel._render?.();
    });
}

export function renderConfiguration(panel, presentation) {
  const module = getAgentConfigModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading configuration…</div>';
  }
  const cacheKey = configRenderCacheKey(panel);
  const cached = getCachedConfigurationMarkup(panel, cacheKey, presentation?.voiceIdentity);
  if (cached !== null) return cached;
  return rememberConfigurationMarkup(panel, cacheKey, module.renderConfiguration(panel, presentation), presentation?.voiceIdentity);
}

export function bindConfiguration(panel) {
  const module = getAgentConfigModule();
  if (!module) return queueRender(panel);
  const result = module.bindConfiguration(panel);
  bindBackupTransfer(panel, backupSummaryLines);
  bindExposedAttributeSettings(panel);
  return result;
}

export function renderTools(panel, presentation) {
  const module = getAgentConfigModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Functions…</div>';
  }
  return module.renderTools(panel, presentation);
}

export function bindTools(panel) {
  const module = getAgentConfigModule();
  if (!module) return queueRender(panel);
  return module.bindTools(panel);
}

export function reconcileTools(panel, presentation) {
  return getAgentConfigModule()?.reconcileTools(panel, presentation) || false;
}

export function configurationDialogs(...args) {
  return getAgentConfigModule()?.configurationDialogs(...args) || "";
}

export function restoreDialog(...args) {
  return getAgentConfigModule()?.restoreDialog(...args) || "";
}

export function configurationChoiceLabel(...args) {
  return requiredImplementation("configurationChoiceLabel").configurationChoiceLabel(...args);
}

export function copyTextToClipboard(...args) {
  return requiredImplementation("copyTextToClipboard").copyTextToClipboard(...args);
}

export function functionGroupIdFromName(...args) {
  return requiredImplementation("functionGroupIdFromName").functionGroupIdFromName(...args);
}

export function isFunctionToolEnabled(...args) {
  return requiredImplementation("isFunctionToolEnabled").isFunctionToolEnabled(...args);
}

export function functionToolCountLabel(...args) {
  return requiredImplementation("functionToolCountLabel").functionToolCountLabel(...args);
}

export function backupSummaryLines(...args) {
  return [...requiredImplementation("backupSummaryLines").backupSummaryLines(...args), BACKUP_CREDENTIAL_WARNING];
}

export function canReplaceToolYamlWithoutConfirmation(...args) {
  return requiredImplementation("canReplaceToolYamlWithoutConfirmation").canReplaceToolYamlWithoutConfirmation(...args);
}

export function matchesFunctionSearch(...args) {
  return requiredImplementation("matchesFunctionSearch").matchesFunctionSearch(...args);
}

export function deleteFunctionGroup(...args) {
  return requiredImplementation("deleteFunctionGroup").deleteFunctionGroup(...args);
}

export function categorizeFunctionTools(...args) {
  return requiredImplementation("categorizeFunctionTools").categorizeFunctionTools(...args);
}

export function saveBar(...args) {
  return requiredImplementation("saveBar").saveBar(...args);
}

export function synchronizePersistedFunctions(...args) {
  return requiredImplementation("synchronizePersistedFunctions").synchronizePersistedFunctions(...args);
}
