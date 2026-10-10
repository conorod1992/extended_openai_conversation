// datetime-local values are wall times in Home Assistant's configured timezone.
export function scheduleWallTime(value, timeZone = "UTC") {
  if (!value) return "";
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(value)).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function scheduleInstant(value, timeZone = "UTC", original) {
  if (original && value === scheduleWallTime(original, timeZone)) return original;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error("Choose a valid date and time.");
  const [, year, month, day, hour, minute] = match.map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  if (new Date(wall).toISOString().slice(0, 16) !== value) throw new Error("Choose a valid date and time.");
  const candidates = new Set();
  // Sample both sides of timezone transitions, then verify each candidate.
  for (let hours = -36; hours <= 36; hours += 6) {
    const sample = wall + hours * 3600000;
    const local = scheduleWallTime(sample, timeZone);
    const offset = Date.parse(`${local}:00Z`) - sample;
    const instant = new Date(wall - offset).toISOString();
    if (scheduleWallTime(instant, timeZone) === value) candidates.add(instant);
  }
  if (!candidates.size) throw new Error("This time does not exist in Home Assistant's timezone because the clocks change. Choose another time.");
  if (candidates.size > 1) throw new Error("This time occurs twice in Home Assistant's timezone because the clocks change. Choose an unambiguous time.");
  return [...candidates][0];
}
