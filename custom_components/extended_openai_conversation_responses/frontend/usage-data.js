const DAILY_PAGE_SIZE = 366;
const MAX_DAILY_PAGES = 128;
const DATE_KEY = /^(\d{4})-(\d{2})-(\d{2})$/;
const DEFAULT_USAGE_WINDOW = "30";

function addUsageCalendarDays(value, amount) {
  const match = DATE_KEY.exec(String(value || ""));
  if (!match || !Number.isInteger(amount)) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) return null;
  date.setUTCDate(date.getUTCDate() + amount);
  return `${String(date.getUTCFullYear()).padStart(4, "0")}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

function localUsageDateKey(panel, value = new Date()) {
  const timeZone = panel?._hass?.config?.time_zone;
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        year: "numeric", month: "2-digit", day: "2-digit", timeZone,
      }).formatToParts(value).filter((item) => item.type !== "literal").map((item) => [item.type, item.value]),
    );
    return `${parts.year}-${parts.month}-${parts.day}`;
  } catch (_) {
    return value.toISOString().slice(0, 10);
  }
}

function usageWindowBounds(window, today) {
  const key = String(window || DEFAULT_USAGE_WINDOW);
  if (key === "all") return {startDate: "0000-01-01", endDate: today};
  if (key === "year") return {startDate: `${today.slice(0, 4)}-01-01`, endDate: today};
  const days = ["7", "30", "90"].includes(key) ? Number(key) : Number(DEFAULT_USAGE_WINDOW);
  return {startDate: addUsageCalendarDays(today, -(days - 1)), endDate: today};
}

export async function loadAllUsageDays(
  callPage,
  {pageSize = DAILY_PAGE_SIZE, maxPages = MAX_DAILY_PAGES, startDate = "0000-01-01", endDate = "9999-12-31"} = {},
) {
  const rows = [];
  let cursor = startDate;
  for (let page = 0; page < maxPages; page += 1) {
    const response = await callPage(cursor, endDate);
    const current = Array.isArray(response?.days) ? response.days : [];
    rows.push(...current);
    if (response?.has_more === false || current.length < pageSize) return {days: rows};
    const lastDate = String(current.at(-1)?.date || "");
    const nextDate = addUsageCalendarDays(lastDate, 1);
    if (!nextDate || nextDate <= cursor || nextDate > endDate) break;
    cursor = nextDate;
  }
  throw new Error(
    "Daily usage history is larger than the bounded management transfer can safely load",
  );
}

function usageCache(panel) {
  panel._eocUsageWindowCache ||= new Map();
  return panel._eocUsageWindowCache;
}

function usageCacheKey(panel, window, today) {
  return `${panel._agentId || ""}|${window}|${today}`;
}

export async function loadUsageWindow(
  panel,
  window = DEFAULT_USAGE_WINDOW,
  today = localUsageDateKey(panel),
  {useCache = true} = {},
) {
  const agent = panel._selectedAgent?.();
  const identity = agent
    ? {entry_id: agent.entry_id, subentry_id: agent.subentry_id}
    : {};
  const key = usageCacheKey(panel, window, today);
  const cache = usageCache(panel);
  if (useCache && cache.has(key)) return cache.get(key);

  const {startDate, endDate} = usageWindowBounds(window, today);
  const pending = window === "all"
    ? loadAllUsageDays((pageStart, pageEnd) => panel._call("usage", "daily", {
        ...identity,
        start_date: pageStart,
        end_date: pageEnd,
      }), {startDate, endDate})
    : panel._call("usage", "daily", {
        ...identity,
        start_date: startDate,
        end_date: endDate,
      });

  const response = await pending;
  const result = {
    ...response,
    requested_window: String(window || DEFAULT_USAGE_WINDOW),
    complete_history: window === "all",
  };
  cache.set(key, result);
  return result;
}

export function loadUsageDaily(panel, extra = {}) {
  if (extra.start_date || extra.end_date) {
    const agent = panel._selectedAgent?.();
    const identity = agent
      ? {entry_id: agent.entry_id, subentry_id: agent.subentry_id}
      : {};
    return panel._call("usage", "daily", {...extra, ...identity});
  }
  return loadUsageWindow(panel, DEFAULT_USAGE_WINDOW, localUsageDateKey(panel), {useCache: false});
}
