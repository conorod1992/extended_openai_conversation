const DAILY_PAGE_SIZE = 366;
const MAX_DAILY_PAGES = 128;
const DATE_KEY = /^(\d{4})-(\d{2})-(\d{2})$/;

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

export async function loadAllUsageDays(
  callPage,
  {pageSize = DAILY_PAGE_SIZE, maxPages = MAX_DAILY_PAGES} = {},
) {
  const rows = [];
  let startDate = "0000-01-01";
  const endDate = "9999-12-31";
  for (let page = 0; page < maxPages; page += 1) {
    const response = await callPage(startDate, endDate);
    const current = Array.isArray(response?.days) ? response.days : [];
    rows.push(...current);
    if (current.length < pageSize) return {days: rows};
    const lastDate = String(current.at(-1)?.date || "");
    const nextDate = addUsageCalendarDays(lastDate, 1);
    if (!nextDate || nextDate <= startDate || nextDate > endDate) break;
    startDate = nextDate;
  }
  throw new Error(
    "Daily usage history is larger than the bounded management transfer can safely load",
  );
}

export function loadUsageDaily(panel, extra = {}) {
  const agent = panel._selectedAgent?.();
  const identity = agent
    ? {entry_id: agent.entry_id, subentry_id: agent.subentry_id}
    : {};
  return loadAllUsageDays((startDate, endDate) => panel._call("usage", "daily", {
    ...extra,
    ...identity,
    start_date: startDate,
    end_date: endDate,
  }));
}

export function loadInputFootprintData(panel, {reusePending = true} = {}) {
  const agentId = panel._agentId;
  if (!agentId || panel._viewKey?.() !== "usage-maintenance/usage") return null;
  if (
    reusePending
    && panel._inputFootprintPromise
    && panel._inputFootprintPendingAgentId === agentId
  ) return panel._inputFootprintPromise;

  const requestToken = (panel._inputFootprintRequestToken || 0) + 1;
  panel._inputFootprintRequestToken = requestToken;
  panel._inputFootprintPendingAgentId = agentId;
  panel._inputFootprintLoading = true;
  panel._inputFootprintError = null;

  const pending = panel._call("usage", "footprint")
    .then((result) => {
      if (
        panel._agentId !== agentId
        || panel._viewKey?.() !== "usage-maintenance/usage"
        || panel._inputFootprintRequestToken !== requestToken
      ) return;
      panel._inputFootprint = result;
      panel._inputFootprintAgentId = agentId;
    })
    .catch((err) => {
      if (
        panel._agentId !== agentId
        || panel._viewKey?.() !== "usage-maintenance/usage"
        || panel._inputFootprintRequestToken !== requestToken
      ) return;
      panel._inputFootprint = null;
      panel._inputFootprintAgentId = agentId;
      panel._inputFootprintError = err?.message || String(err);
    })
    .finally(() => {
      if (
        panel._agentId === agentId
        && panel._viewKey?.() === "usage-maintenance/usage"
        && panel._inputFootprintRequestToken === requestToken
      ) {
        panel._inputFootprintLoading = false;
      }
      if (panel._inputFootprintPromise === pending) {
        panel._inputFootprintPromise = null;
        panel._inputFootprintPendingAgentId = null;
      }
    });

  panel._inputFootprintPromise = pending;
  return pending;
}
