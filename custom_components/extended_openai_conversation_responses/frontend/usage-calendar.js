export const USAGE_WINDOW_OPTIONS = [
  {id: "7", label: "7 days"},
  {id: "30", label: "30 days"},
  {id: "90", label: "90 days"},
  {id: "year", label: "Year to date"},
  {id: "all", label: "All available"},
];
export const DEFAULT_USAGE_WINDOW = "30";
const DATE_KEY = /^(\d{4})-(\d{2})-(\d{2})$/;
const usageDateKeyFormatters = new Map();
const usageDisplayFormatters = new Map();

function cachedDateTimeFormat(cache, locales, options) {
  const key = JSON.stringify([locales || null, options]);
  let formatter = cache.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locales, options);
    cache.set(key, formatter);
  }
  return formatter;
}

export function normalizeUsageWindow(value) {
  const key = String(value || "");
  return USAGE_WINDOW_OPTIONS.some((item) => item.id === key) ? key : DEFAULT_USAGE_WINDOW;
}

export function dateParts(value) {
  const match = DATE_KEY.exec(String(value || ""));
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) return null;
  return {year, month, day};
}

function dateKeyFromUtc(date) {
  return `${String(date.getUTCFullYear()).padStart(4, "0")}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}

export function addUsageCalendarDays(value, amount) {
  const parts = dateParts(value);
  if (!parts || !Number.isInteger(amount)) return null;
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
  date.setUTCDate(date.getUTCDate() + amount);
  return dateKeyFromUtc(date);
}

export function localUsageDateKey(value = new Date(), timeZone = undefined) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  try {
    const parts = Object.fromEntries(
      cachedDateTimeFormat(usageDateKeyFormatters, "en-US", {
        year: "numeric", month: "2-digit", day: "2-digit", timeZone,
      }).formatToParts(date).filter((item) => item.type !== "literal").map((item) => [item.type, item.value]),
    );
    return `${parts.year}-${parts.month}-${parts.day}`;
  } catch (_) {
    return date.toISOString().slice(0, 10);
  }
}

export function usageWindowBounds(window, today) {
  const id = normalizeUsageWindow(window);
  const validToday = dateParts(today) ? today : localUsageDateKey();
  const label = USAGE_WINDOW_OPTIONS.find((item) => item.id === id)?.label || "30 days";
  if (id === "all") return {id, label, startDate: null, endDate: validToday};
  if (id === "year") return {id, label, startDate: `${validToday.slice(0, 4)}-01-01`, endDate: validToday};
  const days = Number(id);
  return {id, label, startDate: addUsageCalendarDays(validToday, -(days - 1)), endDate: validToday};
}

export function formatUsageDate(value, locales = undefined, monthOnly = false) {
  const parts = dateParts(monthOnly ? `${value}-01` : value);
  if (!parts) return String(value || "");
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day, 12));
  try {
    const options = monthOnly
      ? {year: "numeric", month: "short", timeZone: "UTC"}
      : {year: "numeric", month: "short", day: "numeric", timeZone: "UTC"};
    return cachedDateTimeFormat(usageDisplayFormatters, locales, options).format(date);
  } catch (_) {
    return String(value || "");
  }
}

export function usageCalendarToday(panel) {
  // Relative periods use HA's current local calendar. Activity rows and retained
  // summary reads describe usage; neither can establish today's date or the
  // bounds of a subsequent read after a failed or delayed refresh.
  return localUsageDateKey(new Date(), panel?._hass?.config?.time_zone);
}
