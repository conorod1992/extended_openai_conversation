const EMPTY_VALUE = "—";

function validDate(value: string | number | Date): Date | null {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatNumber(value: number | null | undefined, locale?: string): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat(locale).format(value)
    : EMPTY_VALUE;
}

export function formatDateTime(
  value: string | number | Date | null | undefined,
  locale?: string,
): string {
  if (value === null || value === undefined) return EMPTY_VALUE;
  const date = validDate(value);
  return date
    ? new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date)
    : EMPTY_VALUE;
}

export function formatRelativeTime(
  value: string | number | Date | null | undefined,
  now = Date.now(),
  locale?: string,
): string {
  if (value === null || value === undefined) return EMPTY_VALUE;
  const date = validDate(value);
  if (!date) return EMPTY_VALUE;

  const deltaSeconds = (date.getTime() - now) / 1000;
  const absoluteSeconds = Math.abs(deltaSeconds);
  if (absoluteSeconds < 45) return "now";

  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["year", 365 * 24 * 60 * 60],
    ["month", 30 * 24 * 60 * 60],
    ["day", 24 * 60 * 60],
    ["hour", 60 * 60],
    ["minute", 60],
  ];
  const selected: [Intl.RelativeTimeFormatUnit, number] =
    units.find(([, size]) => absoluteSeconds >= size) ?? ["minute", 60];
  const [unit, seconds] = selected;
  const amount = Math.round(deltaSeconds / seconds);
  return new Intl.RelativeTimeFormat(locale, {numeric: "auto"}).format(amount, unit);
}

export function errorMessage(error: unknown, fallback = "Something went wrong"): string {
  if (error instanceof Error && error.message.trim()) return error.message.trim();
  if (typeof error === "string" && error.trim()) return error.trim();
  return fallback;
}
