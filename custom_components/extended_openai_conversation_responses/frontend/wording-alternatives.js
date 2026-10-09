// CSV preserves commas and quotes inside a single spoken alternative.
export function formatWordingAlternatives(values) {
  return values.map((value) => /[",]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value).join(", ");
}

export function parseWordingAlternatives(text) {
  const values = [];
  let value = "", quoted = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '"') {
      if (quoted && text[index + 1] === '"') { value += '"'; index++; }
      else quoted = !quoted;
    } else if (char === "," && !quoted) { if (value.trim()) values.push(value.trim()); value = ""; }
    else value += char;
  }
  if (quoted) throw new Error("Close the quotation marks around this wording alternative.");
  if (value.trim()) values.push(value.trim());
  return values;
}
