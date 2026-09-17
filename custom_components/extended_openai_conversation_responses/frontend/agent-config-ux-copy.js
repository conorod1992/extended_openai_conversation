function replaceText(html, from, to) {
  return String(html || "").replace(from, to);
}

export function polishConfigurationCopy(panel, html) {
  let result = String(html || "");

  result = replaceText(
    result,
    "Choose whether the assistant remembers the recent conversation when you speak to it again.",
    "Control how conversations continue between separate Assist requests.",
  );
  result = replaceText(
    result,
    '<small>Lets a new Assist request continue from your recent conversation instead of starting from scratch.</small>',
    "",
  );

  return result;
}
