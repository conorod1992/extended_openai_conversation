const tokenCount = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
};

export function tokenBreakdown(totalTokens, cachedInputTokens) {
  const total = tokenCount(totalTokens);
  const cached = Math.min(total, tokenCount(cachedInputTokens));
  return { total, cached, uncached: total - cached };
}
