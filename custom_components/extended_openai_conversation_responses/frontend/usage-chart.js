const tokenCount = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
};

export function tokenBreakdown(totalTokens, cachedInputTokens) {
  const total = tokenCount(totalTokens);
  const cached = Math.min(total, tokenCount(cachedInputTokens));
  return { total, cached, uncached: total - cached };
}

export function formatUsageTimestamp(value, locales = undefined) {
  const exact = typeof value === "string" ? value : "";
  const date = new Date(exact);
  if (!exact || Number.isNaN(date.getTime())) {
    return {display: exact || "Unknown", datetime: exact};
  }
  try {
    return {
      display: new Intl.DateTimeFormat(locales, {
        year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit",
      }).format(date),
      datetime: exact,
    };
  } catch (_) {
    return {display: date.toLocaleString(), datetime: exact};
  }
}
