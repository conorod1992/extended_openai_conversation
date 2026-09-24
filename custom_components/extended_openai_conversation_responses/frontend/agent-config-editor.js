import {prepareConfigurationSections, renderConfigurationShell, renderConfigurationActions as renderConfigurationActionsMarkup} from "./agent-config-editor-base.js";
export {BACKUP_CREDENTIAL_WARNING, backupSummaryLines} from "./backup-summary.js";
import {bindConfiguration as bindModelConfiguration} from "./agent-config-editor-model-v2.js";
import {configurationSectionFamily, getRouteFeature} from "./management-route.js";
export {configurationDialogs} from "./agent-config-editor-base.js";

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
  const family = configurationSectionFamily(panel._viewKey?.(), panel._configSections);
  const renderer = getRouteFeature(family);
  if (!renderer) return "";
  prepareConfigurationSections(panel);
  const sections = renderer.renderConfigurationSections(panel, presentation);
  return rememberConfigurationMarkup(panel, cacheKey, renderConfigurationShell(panel, sections), presentation?.voiceIdentity);
}

export function renderConfigurationActions(panel, sections) {
  return renderConfigurationActionsMarkup(panel, sections);
}

export function bindConfiguration(panel) {
  const result = bindModelConfiguration(panel);
  return result;
}
