import {renderConfiguration as renderConfigurationMarkup, renderConfigurationActions as renderConfigurationActionsMarkup, backupSummaryLines as summaryLines} from "./agent-config-editor-base.js";
import {bindConfiguration as bindModelConfiguration} from "./agent-config-editor-model-v2.js";
export {configurationDialogs} from "./agent-config-editor-base.js";

export const BACKUP_CREDENTIAL_WARNING = "Recognised API keys, tokens, passwords, authorization headers and other common secrets are redacted from full backups. Re-enter any required credentials after restore. Redaction is best-effort, so review backup files before sharing them.";
const CACHEABLE_CONFIG_SECTIONS = new Set(["capabilities", "archive", "voice", "speech", "context", "retention", "backup"]);
const MAX_CONFIG_RENDER_CACHE_ENTRIES = 8;

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

export function renderConfiguration(panel, presentation) {
  const cacheKey = configRenderCacheKey(panel);
  const cached = getCachedConfigurationMarkup(panel, cacheKey, presentation?.voiceIdentity);
  if (cached !== null) return cached;
  return rememberConfigurationMarkup(panel, cacheKey, renderConfigurationMarkup(panel, presentation), presentation?.voiceIdentity);
}

export function renderConfigurationActions(panel, sections) {
  return renderConfigurationActionsMarkup(panel, sections);
}

export function bindConfiguration(panel) {
  const result = bindModelConfiguration(panel);
  return result;
}

export function backupSummaryLines(summary) {
  return [...summaryLines(summary), BACKUP_CREDENTIAL_WARNING];
}
