// Native date/time controls display wall time in Home Assistant's timezone,
// independently of the timezone configured on the browser's computer.
function timezone(panel) {
  return panel._hass?.config?.time_zone || "UTC";
}

export function memoryLocalDateTime(panel, value) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone(panel), year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
}

export function memoryExpiryISO(panel, value, original = null) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) throw new Error("Choose an expiry date and time.");
  const local = value.length === 16 ? `${value}:00` : value;
  // Preserve the instant when editing an unchanged time in a repeated DST hour.
  if (original && memoryLocalDateTime(panel, original) === local) return original;
  const wall = Date.parse(`${local}Z`);
  const candidates = new Set();
  // Gather offsets on both sides of DST transitions and test actual wall times.
  for (let hours = -36; hours <= 36; hours += 6) {
    const probe = wall + hours * 3600000;
    const offset = Date.parse(`${memoryLocalDateTime(panel, new Date(probe).toISOString())}Z`) - probe;
    const instant = new Date(wall - offset).toISOString();
    if (memoryLocalDateTime(panel, instant) === local) candidates.add(instant);
  }
  if (!candidates.size) throw new Error("This time does not exist in Home Assistant’s timezone. Choose another time.");
  // A newly selected repeated hour consistently uses its first occurrence.
  return [...candidates].sort()[0];
}
