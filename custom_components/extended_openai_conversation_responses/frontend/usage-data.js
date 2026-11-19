import {
  DEFAULT_USAGE_WINDOW, FIRST_USAGE_DATE, LAST_USAGE_DATE,
  addUsageCalendarDays, usageCalendarToday, usageWindowBounds,
} from "./usage-calendar.js";

const DAILY_PAGE_SIZE = 366;
const MAX_DAILY_PAGES = 128;

function usageAgentIdentity(panel) {
  const agent = panel._selectedAgent?.();
  return agent ? {entry_id: agent.entry_id, subentry_id: agent.subentry_id} : {};
}

export async function loadAllUsageDays(
  callPage,
  {pageSize = DAILY_PAGE_SIZE, maxPages = MAX_DAILY_PAGES, startDate = FIRST_USAGE_DATE, endDate = LAST_USAGE_DATE} = {},
) {
  const rows = [];
  let localRunSummary;
  let cursor = startDate;
  for (let page = 0; page < maxPages; page += 1) {
    const response = await callPage(cursor, endDate);
    const current = Array.isArray(response?.days) ? response.days : [];
    // Only the first page covers the whole requested range.
    if (page === 0) localRunSummary = response?.local_run_summary;
    rows.push(...current);
    if (response?.has_more === false || current.length < pageSize) return {days: rows, ...(localRunSummary ? {local_run_summary: localRunSummary} : {})};
    const lastDate = String(current.at(-1)?.date || "");
    const nextDate = addUsageCalendarDays(lastDate, 1);
    if (!nextDate || nextDate <= cursor || nextDate > endDate) break;
    cursor = nextDate;
  }
  throw new Error(
    "Daily usage history is larger than the bounded management transfer can safely load",
  );
}

export async function loadUsageWindow(
  panel,
  window = DEFAULT_USAGE_WINDOW,
  today = usageCalendarToday(panel),
) {
  const identity = usageAgentIdentity(panel);
  const {id, startDate, endDate} = usageWindowBounds(window, today);
  const pending = id === "all"
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
  return {
    ...response,
    requested_window: id,
    complete_history: id === "all",
  };
}

export function loadUsageDaily(panel, extra = {}) {
  if (extra.start_date || extra.end_date) {
    return panel._call("usage", "daily", {...extra, ...usageAgentIdentity(panel)});
  }
  // A fresh route read also retires previously selected multi-page history.
  const allDaysKey = `${panel._agentId}|usage-maintenance/usage|all-days`;
  panel._sectionCache?.delete(allDaysKey);
  panel._eocSectionCacheTimes?.delete(allDaysKey);
  return loadUsageWindow(panel, panel._usageHistoryWindow);
}
