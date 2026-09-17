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

  result = replaceText(
    result,
    '<small>Gives the assistant the current Home Assistant-local date and time.</small>',
    "",
  );
  result = replaceText(
    result,
    '<small>Provides the current names and states of entities exposed to Assist.</small>',
    "",
  );
  result = replaceText(
    result,
    "Leave an override blank to use the integration-maintained default. Custom values use the prompt-template environment.",
    "Customize how date, time and device information is added to the prompt. Leave a field blank to use the default.",
  );

  return result;
}
