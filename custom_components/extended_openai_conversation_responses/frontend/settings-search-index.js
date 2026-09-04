import {SETTINGS_INDEX} from "./frontend-navigation.js";

export function buildSettingsSearchProjection(settings = SETTINGS_INDEX) {
  return settings.map((item, index) => {
    const label = String(item.label || "");
    const description = String(item.description || "");
    const terms = String(item.terms || "");
    const configKey = String(item.configKey || "");
    return {
      item,
      index,
      label: label.toLowerCase(),
      haystack: `${label} ${description} ${terms} ${configKey}`.toLowerCase(),
    };
  });
}

export const SETTINGS_SEARCH_PROJECTION = buildSettingsSearchProjection();

export function searchProjectedSettings(query, projection = SETTINGS_SEARCH_PROJECTION) {
  const normalized = String(query || "").trim().toLowerCase();
  const terms = normalized.split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  return projection
    .map((entry) => {
      if (!terms.every((term) => entry.haystack.includes(term))) return null;
      const score = entry.label === normalized ? 0 : entry.label.startsWith(normalized) ? 1 : entry.label.includes(normalized) ? 2 : 3;
      return {item: entry.item, index: entry.index, score};
    })
    .filter(Boolean)
    .sort((a, b) => a.score - b.score || a.index - b.index)
    .map(({item}) => item);
}
