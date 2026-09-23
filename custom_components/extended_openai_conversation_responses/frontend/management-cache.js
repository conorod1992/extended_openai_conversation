// Read-heavy route caches deliberately exclude live memory/history and drafts.
export const SECTION_CACHE_TTL_MS = 30_000;
export const SCOPE_CACHE_TTL_MS = 30_000;
export const CLEAN_CONFIG_TTL_MS = 30_000;

export function readSectionCache(panel, view) {
  const key = panel._sectionCacheKey(view);
  if (!key || !panel._sectionCache.has(key)) return {key, fresh:false};
  const result = panel._sectionCache.get(key);
  const loadedAt = panel._eocSectionCacheTimes.get(key);
  const fresh = Boolean(loadedAt && Date.now() - loadedAt <= SECTION_CACHE_TTL_MS);
  return {key, result, fresh};
}

export function writeSectionCache(panel, key, result) {
  if (!key || result === undefined) return;
  panel._sectionCache.set(key, result);
  panel._eocSectionCacheTimes.set(key, Date.now());
}

export function pruneCacheTimes(panel) {
  for (const [cache, times] of [
    [panel._sectionCache, panel._eocSectionCacheTimes],
    [panel._scopeCatalogCache, panel._eocScopeCatalogTimes],
  ]) {
    if (!times) continue;
    for (const key of times.keys()) if (!cache.has(key)) times.delete(key);
  }
}
