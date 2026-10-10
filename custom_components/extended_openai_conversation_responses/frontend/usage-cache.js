export function invalidateUsageWindows(panel, agentId = panel._agentId || "") {
  panel._eocUsageWindowGenerations ||= new Map();
  const generations = panel._eocUsageWindowGenerations;
  generations.set(agentId, (generations.get(agentId) || 0) + 1);
  for (const key of panel._eocUsageWindowCache?.keys() || []) {
    if (key.startsWith(`${agentId}|`)) panel._eocUsageWindowCache.delete(key);
  }
}
