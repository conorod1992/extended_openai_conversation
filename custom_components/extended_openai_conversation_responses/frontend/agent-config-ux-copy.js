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

  result = replaceText(
    result,
    '<small>Lets the assistant search the web for current information.</small>',
    "",
  );
  result = replaceText(
    result,
    "Choose how much supporting material the provider returns with each web search.",
    "Choose how much information is returned with search results.",
  );
  result = replaceText(
    result,
    "Lets the assistant search reference material you maintain locally; it is separate from memories and conversation history.",
    "Allow the assistant to search your Knowledge Library when useful.",
  );

  result = replaceText(
    result,
    "Keep conversations locally so you can review them or let the assistant find earlier discussions.",
    "Manage saved conversation history and search.",
  );
  result = replaceText(
    result,
    '<small>Stores conversations locally for later review and optional search by the assistant.</small>',
    "",
  );
  result = replaceText(
    result,
    "Start a new archive after (minutes)",
    "New conversation after inactivity (minutes)",
  );
  result = replaceText(
    result,
    "After this much inactivity, the next message is saved as a new archived conversation.",
    "After this much inactivity, the next message starts a new saved conversation.",
  );

  result = replaceText(
    result,
    '<small>Choose whether the request uses no retained personal data, shared household data, a default user, or a device mapping.</small>',
    "",
  );
  result = replaceText(
    result,
    '<small>Choose whose data to use when a voice device is not assigned to a user.</small>',
    "",
  );

  result = replaceText(
    result,
    "Clean text before it is spoken while retaining the original assistant response for history and context.",
    "Adjust text before it is spoken without changing the saved assistant response.",
  );
  result = replaceText(result, "Speech post-processing", "Clean responses for speech");
  result = replaceText(
    result,
    '<small>Cleans text before it is spoken without changing the retained original assistant response.</small>',
    "",
  );
  result = replaceText(
    result,
    "Remove Markdown links and formatting",
    "Remove Markdown formatting",
  );
  result = replaceText(
    result,
    "Removes Markdown links and formatting from progressive and completed spoken output.",
    "Stops formatting and Markdown links from being read aloud.",
  );
  result = replaceText(
    result,
    "Rules run on the completed response. Progressive TTS is disabled when custom rules are configured.",
    "Replace words or patterns in spoken responses. Streaming speech is disabled while custom replacements are active.",
  );

  result = replaceText(
    result,
    "Choose what happens when the conversation becomes too large for the model's context window.",
    "Choose how older conversation history is reduced when it becomes too large.",
  );
  result = replaceText(
    result,
    "Older conversation content is reduced when the provider reports more than this many input tokens.",
    "When history reaches this size, use the trimming method below.",
  );

  return result;
}
